"""Bounded OOXML rich-content extraction. Never executes fields or external URLs."""
from copy import deepcopy
import csv
import hashlib
import io
import os
import posixpath
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
V = "urn:schemas-microsoft-com:vml"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def tag(name):
    return f"{{{W}}}{name}"


class RichContent:
    def __init__(self, context, archive, settings, state):
        self.context, self.archive, self.settings = context, archive, settings
        self.part = "word/document.xml"
        self.anchor = ""
        self.seen = set(state.get("richReferences", []))
        self.notices = list(state.get("notices", []))
        self.relationship_cache = {self.part: context.relationships}
        self.image_cache = {}
        self.assets = []
        self.nested_tables = []
        self.notes = []
        self.field_stack = deepcopy(state.get("fieldStack", []))

    def notice(self, value):
        if value not in self.notices and len(self.notices) < 64:
            self.notices.append(value)

    def relation(self, ident, expected=None):
        from docx_stream import _metadata, REL
        if self.part not in self.relationship_cache:
            folder, name = posixpath.split(self.part)
            root = _metadata(self.archive, f"{folder}/_rels/{name}.rels", self.context.limits["docx_max_metadata_bytes"])
            relations = {}
            for relation in root if root is not None else []:
                ident = relation.get("Id")
                if not ident or ident in relations or not relation.get("Target"):
                    raise ValueError("DOCX_INVALID_DEPENDENCY: malformed part relationship")
                relations[ident] = dict(relation.attrib)
            self.relationship_cache[self.part] = relations
        rel = self.relationship_cache[self.part].get(ident)
        if not rel or (expected and not rel.get("Type", "").endswith("/" + expected)):
            raise ValueError("DOCX_DEPENDENCY_MISSING: relationship " + str(ident))
        if rel.get("TargetMode") == "External":
            return rel, None
        target = rel["Target"].replace("\\", "/")
        path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join(posixpath.dirname(self.part), target))
        if path.startswith("../") or path not in self.archive.namelist():
            raise ValueError("DOCX_DEPENDENCY_MISSING: package part " + path)
        return rel, path

    def image(self, element):
        references = []
        for node in element.iter():
            if node.tag == f"{{{A}}}blip":
                ident = node.get(f"{{{R}}}embed") or node.get(f"{{{R}}}link")
            elif node.tag == f"{{{V}}}imagedata":
                ident = node.get(f"{{{R}}}id")
            else:
                continue
            if ident and ident not in references:
                references.append(ident)
        descriptions = []
        for node in element.iter():
            for key in ("descr", "title"):
                if node.get(key) and node.get(key) not in descriptions:
                    descriptions.append(node.get(key))
        box_texts = []
        for index, ident in enumerate(references):
            rel, path = self.relation(ident, "image")
            meta = {"relationship_id": ident, "part": path, "source_part": self.part,
                    "anchor": self.anchor, "alt_text": "\n".join(descriptions)}
            if path is None:
                meta.update(status="EXTERNAL_NOT_FETCHED", target=rel["Target"])
                self.context.unsupported("EXTERNAL_IMAGE")
                self.assets.append(meta)
                continue
            info = self.archive.getinfo(path)
            if info.file_size > self.context.limits["docx_max_image_bytes"]:
                raise ValueError("DOCX_IMAGE_LIMIT: image exceeds byte limit")
            data = self.archive.read(path)
            digest = hashlib.sha256(data).hexdigest()
            if digest not in self.image_cache:
                self.image_cache[digest] = self.read_image(data, path)
                if len(self.image_cache) > 8:
                    self.image_cache.pop(next(iter(self.image_cache)))
            meta.update(deepcopy(self.image_cache[digest]), sha256=digest, bytes=len(data))
            meta["id"] = hashlib.sha256(f"{self.part}:{self.anchor}:{ident}:{index}".encode()).hexdigest()
            self.assets.append(meta)
        # A drawing may contain an editable text box rather than a raster image.
        for box in element.iter(tag("txbxContent")):
            box_texts.append("\n".join(self.text(p) for p in box.findall(tag("p"))))
        if not references and not box_texts:
            self.context.unsupported("DRAWING_WITHOUT_IMAGE_OR_TEXT")
        return "\n".join(box_texts)

    def read_image(self, data, path):
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if width * height > self.context.limits["docx_max_image_pixels"]:
                    raise ValueError("DOCX_IMAGE_LIMIT: decoded dimensions exceed pixel limit")
                image.seek(0)
                rgba = image.convert("RGBA")
                raster = Image.new("RGB", rgba.size, "white")
                raster.paste(rgba, mask=rgba.getchannel("A"))
                if getattr(image, "n_frames", 1) > 1:
                    self.context.unsupported("MULTIFRAME_IMAGE")
                result = {"format": image.format, "width": width, "height": height,
                          "text": "", "words": [], "status": "RESOURCE_ONLY"}
        except (UnidentifiedImageError, OSError) as error:
            self.context.unsupported("IMAGE_FORMAT")
            return {"status": "UNSUPPORTED_FORMAT", "text": "", "words": []}
        if not self.settings.get("ocr_enabled", False):
            self.notice("DOCX_IMAGE_OCR_DISABLED: image resources and alternative text retained; image text is not recognized")
            return result
        languages = self.settings.get("ocr_languages", ["chi_sim", "eng"])
        if not languages or any(not isinstance(v, str) or not v.replace("_", "").isalnum() for v in languages):
            raise ValueError("DOCX_INVALID_OCR_LANGUAGES")
        try:
            with tempfile.TemporaryDirectory(prefix="docx-image-") as directory:
                source = Path(directory) / "image.png"
                raster.save(source)
                run = subprocess.run(["tesseract", str(source), "stdout", "-l", "+".join(languages),
                                      "--psm", "11", "tsv"], capture_output=True, timeout=self.context.limits["docx_ocr_timeout"],
                                     env={**os.environ, "OMP_THREAD_LIMIT": "2"})
                if run.returncode:
                    raise RuntimeError("DOCX_OCR_FAILED: " + run.stderr.decode(errors="replace")[-400:])
                if len(run.stdout) > self.context.limits["docx_max_node_chars"] * 32:
                    raise ValueError("DOCX_OCR_OUTPUT_LIMIT")
                words, lines = [], {}
                for row in csv.DictReader(io.StringIO(run.stdout.decode("utf-8")), delimiter="\t"):
                    text = (row.get("text") or "").strip()
                    if not text or row.get("level") != "5":
                        continue
                    item = {"text": text, "confidence": float(row["conf"]),
                            "bbox": [int(row[k]) for k in ("left", "top", "width", "height")]}
                    words.append(item)
                    key = tuple(row[k] for k in ("page_num", "block_num", "par_num", "line_num"))
                    lines.setdefault(key, []).append(text)
                result.update(text="\n".join(" ".join(v) for v in lines.values()), words=words,
                              status="OCR_COMPLETE" if words else "OCR_NO_TEXT", coordinate_system="IMAGE_PIXELS")
        except FileNotFoundError as error:
            raise RuntimeError("DOCX_OCR_UNAVAILABLE: install tesseract and configured language packs") from error
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("DOCX_OCR_TIMEOUT: image OCR exceeded time limit") from error
        return result

    def text(self, element):
        """Read saved final-view content in source order, never field instructions."""
        if element is None:
            return ""
        name = element.tag
        if name in {tag("del"), tag("moveFrom"), tag("instrText"), tag("delText"), tag("sdtPr")}:
            return ""
        if name == tag("fldChar"):
            kind = element.get(tag("fldCharType"))
            if kind == "begin":
                if len(self.field_stack) >= 64:
                    raise ValueError("DOCX_FIELD_LIMIT: nesting depth exceeds limit")
                self.field_stack.append("INSTRUCTION")
            elif kind == "separate":
                if not self.field_stack:
                    raise ValueError("DOCX_INVALID_FIELD: separator without begin")
                self.field_stack[-1] = "RESULT"
            elif kind == "end":
                if not self.field_stack:
                    raise ValueError("DOCX_INVALID_FIELD: end without begin")
                self.field_stack.pop()
            else:
                raise ValueError("DOCX_INVALID_FIELD: unknown field boundary")
            return ""
        if name in {tag("t"), f"{{{M}}}t"}:
            return element.text or ""
        if name in {tag("tab"), tag("ptab")}:
            return "\t"
        if name in {tag("br"), tag("cr")}:
            return "\n"
        if name == tag("noBreakHyphen"):
            return "\u2011"
        if name == tag("softHyphen"):
            return "\u00ad"
        if name in {tag("drawing"), tag("pict")}:
            return self.image(element)
        if name == f"{{{MC}}}AlternateContent":
            selected = element.find(f"{{{MC}}}Choice")
            if selected is None:
                selected = element.find(f"{{{MC}}}Fallback")
            return self.text(selected) if selected is not None else ""
        if name == tag("sdt"):
            content = element.find(tag("sdtContent"))
            return self.text(content) if content is not None else ""
        if name in {tag("footnoteReference"), tag("endnoteReference"), tag("commentReference")}:
            return self.note(element)
        if name == tag("sym"):
            return "[符号 " + element.get(tag("font"), "") + ":" + element.get(tag("char"), "") + "]"
        if name == f"{{{M}}}f":
            return "(" + self.text(element.find(f"{{{M}}}num")) + ")/(" + self.text(element.find(f"{{{M}}}den")) + ")"
        if name in {f"{{{M}}}sSup", f"{{{M}}}sSub"}:
            exponent = "sup" if name.endswith("sSup") else "sub"
            return self.text(element.find(f"{{{M}}}e")) + ("^(" if exponent == "sup" else "_(") + self.text(element.find(f"{{{M}}}{exponent}")) + ")"
        return "".join(self.text(child) for child in element) if element is not None else ""

    def note(self, element):
        from docx_stream import _metadata
        kind = element.tag.rsplit("}", 1)[-1].replace("Reference", "")
        ident = element.get(tag("id"))
        key = f"{kind}:{ident}"
        path = "word/" + ("comments.xml" if kind == "comment" else kind + "s.xml")
        metadata = {"kind":kind,"part":path,"id":ident,"reference_anchor":self.anchor,
                    "node_ref":f"docx:part:{path}/{kind}:{ident}"}
        self.notes.append(metadata)
        if key in self.seen:
            metadata["already_emitted"] = True
            return ""
        self.seen.add(key)
        if len(self.seen) > 4096:
            raise ValueError("DOCX_REFERENCE_LIMIT")
        root = _metadata(self.archive, path, self.context.limits["docx_max_metadata_bytes"])
        note = next((n for n in root if n.get(tag("id")) == ident), None) if root is not None else None
        if note is None:
            raise ValueError("DOCX_DEPENDENCY_MISSING: " + key)
        previous = self.part
        self.part = path
        try:
            values=[]
            for index, child in enumerate(note):
                if child.tag == tag("p"):
                    values.append(self.text(child))
                elif child.tag == tag("tbl"):
                    from docx_stream import _table
                    nested = _table(child,self.context,0,metadata["node_ref"]+f"/node:{index}")
                    metadata.setdefault("tables",[]).append(nested)
                    values.append(nested["text"])
            metadata["text"]="\n".join(values)
            return "[" + kind + " " + str(ident) + "] " + metadata["text"]
        finally:
            self.part = previous

    def paragraph(self, element):
        before = len(self.assets)
        first_note = len(self.notes)
        value = self.text(element)
        fields = [n.text or "" for n in element.iter(tag("instrText"))]
        fields += [n.get(tag("instr"), "") for n in element.iter(tag("fldSimple"))]
        if fields or any(n.tag == tag("fldChar") for n in element.iter()):
            self.notice("DOCX_FIELDS_CACHED: saved field results retained; fields are not recalculated or executed")
        metadata = {"images": deepcopy(self.assets[before:])} if len(self.assets) > before else {}
        if len(self.notes)>first_note:
            metadata["notes"] = deepcopy(self.notes[first_note:])
        if fields:
            metadata["fields"] = [{"instruction": f, "evaluation": "SAVED_RESULT_ONLY"} for f in fields]
        formulas = list(element.iter(f"{{{M}}}oMath"))
        if formulas:
            metadata["formulas"] = [{"ooxml": ET.tostring(n, encoding="unicode"), "text": self.text(n),
                                     "representation": "OMML_LINEAR_TEXT"} for n in formulas]
        if any(n.tag in {tag("ins"), tag("del"), tag("moveFrom"), tag("moveTo")} for n in element.iter()):
            metadata["revision_view"] = "FINAL_SAVED_VIEW"
            self.notice("DOCX_REVISIONS_FINAL_VIEW: deleted and moved-from content excluded")
        return value, metadata
