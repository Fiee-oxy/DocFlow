"""Bounded OOXML streaming with local image OCR and no chat LLM.

Paragraphs and complete top-level tables are atomic source units. ZIP/XML input
is scanned sequentially on resume; already committed units are not rebuilt.
Word page numbers are deliberately absent. Native tables, rich text, ancillary
parts and image resources are preserved; unsupported binary content fails in
strict mode. This is not a general Word rendering or field execution engine.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
PARSER = "DOCX_STREAM"
PARSE_UNIT = "DOCX_BODY_NODE"
STATE_VERSION = 2
BASE_WARNING = "DOCX 增量解析：按完整段落和表格处理，不提供 Word 渲染页码；域使用保存结果，修订使用最终视图；图片文字由本地 OCR 识别，图表语义不等于 OCR。"


def w(name):
    return f"{{{W}}}{name}"


def _val(element, default=None):
    return element.get(w("val"), default) if element is not None else default


def _int(value, name, minimum=0, maximum=10**9):
    if isinstance(value, bool):
        raise ValueError(f"DOCX_INVALID_{name}: integer required")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"DOCX_INVALID_{name}: integer required") from error
    if str(number) != str(value) or not minimum <= number <= maximum:
        raise ValueError(f"DOCX_INVALID_{name}: outside {minimum}..{maximum}")
    return number


def _limits(settings):
    defaults = {
        "docx_batch_nodes": 32, "docx_batch_chars": 12000,
        "docx_max_node_chars": 200000, "docx_max_node_xml_bytes": 16 * 1024 * 1024,
        "docx_max_xml_bytes": 128 * 1024 * 1024, "docx_max_metadata_bytes": 8 * 1024 * 1024,
        "docx_max_uncompressed_bytes": 512 * 1024 * 1024, "docx_max_zip_entries": 4096,
        "docx_max_table_cells": 20000, "docx_max_body_nodes": 500000,
        "docx_max_image_bytes": 32 * 1024 * 1024, "docx_max_image_pixels": 40000000,
        "docx_ocr_timeout": 30,
        "max_chars": 20_000_000, "max_bytes": 2 * 1024 * 1024 * 1024,
    }
    limits = {name: _int(settings.get(name, default), name.upper(), 1, 2**40)
              for name, default in defaults.items()}
    strict = settings.get("docx_strict", True)
    if not isinstance(strict, bool):
        raise ValueError("DOCX_INVALID_STRICT: boolean required")
    limits["docx_strict"] = strict
    return limits


class _SafeXMLReader:
    """Limit expanded ZIP bytes and reject DTD/entities, including split reads."""
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit
        self.count = 0
        self.tail = b""
        self.prefix = b""
        self.node_start = None
        self.node_limit = None

    def read(self, size=-1):
        data = self.stream.read(size)
        self.count += len(data)
        if self.count > self.limit:
            raise ValueError("DOCX_XML_LIMIT: expanded XML exceeds limit")
        if self.node_start is not None and self.count - self.node_start > self.node_limit + 32768:
            raise ValueError("DOCX_NODE_XML_LIMIT: complete node exceeds hard limit")
        scanned = (self.tail + data).upper()
        # OOXML produced by Word is UTF-8. Reject UTF-16/32 rather than allowing
        # NUL-interleaved DTD tokens to evade this guard.
        if b"\x00" in data or b"<!DOCTYPE" in scanned or b"<!ENTITY" in scanned:
            raise ValueError("DOCX_UNSAFE_XML: DTD, entities and UTF-16/32 are not accepted")
        if len(self.prefix) < 512:
            self.prefix = (self.prefix + data)[:512]
            encoding = re.search(br"encoding\s*=\s*['\"]([^'\"]+)['\"]", self.prefix, re.I)
            if encoding and encoding.group(1).lower() not in {b"utf-8", b"utf8", b"us-ascii", b"ascii"}:
                raise ValueError("DOCX_UNSUPPORTED_XML_ENCODING: UTF-8 required")
        self.tail = (self.tail + data)[-32:]
        return data


def _metadata(archive, name, limit):
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > limit:
        raise ValueError(f"DOCX_METADATA_LIMIT: {name}")
    with archive.open(info) as stream:
        guarded = _SafeXMLReader(stream, limit)
        try:
            return ET.parse(guarded).getroot()
        except ET.ParseError as error:
            raise ValueError(f"DOCX_INVALID_XML: {name}") from error


def _validate_zip(archive, limits):
    entries = archive.infolist()
    names = [entry.filename for entry in entries]
    if len(entries) > limits["docx_max_zip_entries"] or len(names) != len(set(names)):
        raise ValueError("DOCX_ZIP_LIMIT: too many or duplicate archive entries")
    if sum(entry.file_size for entry in entries) > limits["docx_max_uncompressed_bytes"]:
        raise ValueError("DOCX_ZIP_LIMIT: expanded archive exceeds limit")
    for entry in entries:
        if entry.flag_bits & 1:
            raise ValueError("DOCX_ENCRYPTED_ZIP: encrypted entries are unsupported")
        if entry.filename.startswith(("/", "\\")) or ".." in entry.filename.replace("\\", "/").split("/"):
            raise ValueError("DOCX_UNSAFE_ZIP_PATH: invalid archive member")
        if entry.file_size and (entry.compress_size == 0 or entry.file_size > 1000 * entry.compress_size):
            raise ValueError("DOCX_ZIP_LIMIT: suspicious compression ratio")
    try:
        document = archive.getinfo("word/document.xml")
    except KeyError as error:
        raise ValueError("DOCX_DEPENDENCY_MISSING: word/document.xml") from error
    if document.file_size > limits["docx_max_xml_bytes"]:
        raise ValueError("DOCX_XML_LIMIT: document.xml exceeds limit")


def _source_hash(path, maximum):
    if path.stat().st_size > maximum:
        raise ValueError("DOCX_FILE_LIMIT: compressed file exceeds limit")
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for piece in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(piece)
    return value.hexdigest()


def _num_properties(ppr):
    if ppr is None:
        return {}
    num = ppr.find(w("numPr"))
    if num is None:
        return {}
    result = {}
    for name in ("numId", "ilvl"):
        value = _val(num.find(w(name)))
        if value is not None:
            result[name] = value
    return result


class _Context:
    def __init__(self, archive, limits, state, settings=None):
        self.limits = limits
        self.heading = list(state.get("heading", []))
        self.counters = deepcopy(state.get("numberingCounters", {}))
        self.warnings = list(state.get("warnings", []))
        self.styles = {}
        self.default_style = None
        styles = _metadata(archive, "word/styles.xml", limits["docx_max_metadata_bytes"])
        if styles is not None:
            if styles.tag != w("styles"):
                raise ValueError("DOCX_INVALID_DEPENDENCY: styles.xml root")
            for style in styles.findall(w("style")):
                name = style.get(w("styleId"))
                if not name or name in self.styles:
                    raise ValueError("DOCX_INVALID_DEPENDENCY: missing/duplicate style id")
                self.styles[name] = style
                if style.get(w("type")) == "paragraph" and style.get(w("default")) in {"1", "true", "on"}:
                    self.default_style = name
            if len(self.styles) > 10000:
                raise ValueError("DOCX_METADATA_LIMIT: too many styles")
        self.numbers, self.abstracts = {}, {}
        numbering = _metadata(archive, "word/numbering.xml", limits["docx_max_metadata_bytes"])
        if numbering is not None:
            if numbering.tag != w("numbering"):
                raise ValueError("DOCX_INVALID_DEPENDENCY: numbering.xml root")
            for abstract in numbering.findall(w("abstractNum")):
                ident = abstract.get(w("abstractNumId"))
                if ident is None or ident in self.abstracts:
                    raise ValueError("DOCX_INVALID_DEPENDENCY: duplicate abstract numbering")
                self.abstracts[ident] = abstract
            for number in numbering.findall(w("num")):
                ident = number.get(w("numId"))
                if ident is None or ident in self.numbers:
                    raise ValueError("DOCX_INVALID_DEPENDENCY: duplicate numbering id")
                self.numbers[ident] = number
        self.relationships = {}
        relations = _metadata(archive, "word/_rels/document.xml.rels", limits["docx_max_metadata_bytes"])
        if relations is not None:
            if relations.tag != f"{{{REL}}}Relationships":
                raise ValueError("DOCX_INVALID_DEPENDENCY: document relationships root")
            for relation in relations:
                ident = relation.get("Id")
                if not ident or ident in self.relationships or not relation.get("Target"):
                    raise ValueError("DOCX_INVALID_DEPENDENCY: malformed relationship")
                self.relationships[ident] = dict(relation.attrib)
        from docx_rich import RichContent
        self.rich = RichContent(self, archive, settings or {}, state)

    def unsupported(self, code):
        warning = f"DOCX_UNSUPPORTED_{code}: use the normal Docling parser for this document"
        if self.limits["docx_strict"]:
            raise ValueError(warning)
        if warning not in self.warnings:
            self.warnings.append(warning)

    def check_features(self, element):
        unsupported = {
            w("object"): "EMBEDDED_OBJECT", w("altChunk"): "ALT_CHUNK",
        }
        def visit(node):
            if node.tag in {w("del"), w("moveFrom")}:
                return
            if node.tag in unsupported:
                self.unsupported(unsupported[node.tag])
            for child in node:
                visit(child)
        visit(element)

    def style_properties(self, ident, visiting=None):
        if ident is None:
            return {}
        if ident not in self.styles:
            raise ValueError(f"DOCX_DEPENDENCY_MISSING: style {ident}")
        visiting = set(visiting or [])
        if ident in visiting or len(visiting) >= 64:
            raise ValueError("DOCX_INVALID_DEPENDENCY: cyclic/deep style inheritance")
        visiting.add(ident)
        style = self.styles[ident]
        parent = _val(style.find(w("basedOn")))
        result = self.style_properties(parent, visiting) if parent else {}
        ppr = style.find(w("pPr"))
        outline = _val(ppr.find(w("outlineLvl"))) if ppr is not None else None
        name = _val(style.find(w("name")), ident)
        if outline is None:
            match = re.fullmatch(r"(?:heading|标题|標題)\s*([1-9])", name or "", re.I)
            if match:
                outline = str(int(match.group(1)) - 1)
        if outline is not None:
            result["outline"] = _int(outline, "OUTLINE", 0, 9)
        result.update(_num_properties(ppr))
        return result

    def numbering(self, properties):
        ident = properties.get("numId")
        if ident in {None, "0"}:
            return None
        if ident not in self.numbers:
            raise ValueError(f"DOCX_DEPENDENCY_MISSING: numbering {ident}")
        level = _int(properties.get("ilvl", "0"), "LIST_LEVEL", 0, 8)
        number = self.numbers[ident]
        abstract_id = _val(number.find(w("abstractNumId")))
        abstract = self.abstracts.get(abstract_id)
        if abstract is None:
            raise ValueError("DOCX_DEPENDENCY_MISSING: abstract numbering")
        levels = {}
        for node in abstract.findall(w("lvl")):
            index = _int(node.get(w("ilvl"), "0"), "LIST_LEVEL", 0, 8)
            if index in levels:
                raise ValueError("DOCX_INVALID_DEPENDENCY: duplicate numbering level")
            levels[index] = node
        if level not in levels:
            raise ValueError("DOCX_DEPENDENCY_MISSING: numbering level")
        definition = levels[level]
        start = _int(_val(definition.find(w("start")), "1"), "LIST_START", 0)
        starts = {i: _int(_val(n.find(w("start")), "1"), "LIST_START", 0) for i,n in levels.items()}
        for override in number.findall(w("lvlOverride")):
            override_level = _int(override.get(w("ilvl"), "0"), "LIST_LEVEL", 0, 8)
            nested = override.find(w("lvl"))
            if nested is not None:
                levels[override_level] = nested
                starts[override_level] = _int(_val(nested.find(w("start")), "1"), "LIST_START", 0)
            if override.find(w("startOverride")) is not None:
                starts[override_level] = _int(_val(override.find(w("startOverride"))), "LIST_START", 0)
        definition = levels[level]
        start = starts[level]
        counters = self.counters.setdefault(ident, {})
        counters[str(level)] = counters.get(str(level), start - 1) + 1
        for key in list(counters):
            child_definition = levels.get(int(key))
            restart = _val(child_definition.find(w("lvlRestart"))) if child_definition is not None else None
            if int(key) > level and restart != "0" and (restart is None or level < int(restart)):
                del counters[key]
        fmt = _val(definition.find(w("numFmt")), "decimal")
        pattern = _val(definition.find(w("lvlText")), "%1.")
        supported = {"decimal", "bullet", "none", "lowerLetter", "upperLetter", "lowerRoman", "upperRoman", "chineseCounting"}
        if fmt not in supported:
            self.unsupported("NUMBER_FORMAT")
        def render(match):
            index = int(match.group(1)) - 1
            parent = levels.get(index)
            if parent is None:
                raise ValueError("DOCX_DEPENDENCY_MISSING: parent numbering level")
            value = counters.get(str(index), starts.get(index, 1))
            form = _val(parent.find(w("numFmt")), "decimal")
            if definition.find(w("isLgl")) is not None:
                form = "decimal"
            if form not in supported:
                self.unsupported("NUMBER_FORMAT")
            if form == "chineseCounting" and not 0 <= value <= 9999:
                self.unsupported("NUMBER_FORMAT_RANGE")
                return str(value)
            return _format_number(value, form)
        marker = "" if fmt == "none" else re.sub(r"%([1-9])", render, pattern)
        return {"marker": marker, "enumerated": fmt != "bullet", "list_level": level, "numbering_id": ident}

    def paragraph(self, paragraph, *, update_heading=True):
        ppr = paragraph.find(w("pPr"))
        ident = _val(ppr.find(w("pStyle"))) if ppr is not None else None
        properties = self.style_properties(ident or self.default_style)
        if ppr is not None:
            outline = _val(ppr.find(w("outlineLvl")))
            if outline is not None:
                properties["outline"] = _int(outline, "OUTLINE", 0, 9)
            properties.update(_num_properties(ppr))
        value, rich_metadata = self.rich.paragraph(paragraph)
        hyperlinks = []
        for node in paragraph.iter():
            if node.tag == w("hyperlink"):
                relation = node.get(f"{{{R}}}id")
                if relation:
                    target, _ = self.rich.relation(relation, "hyperlink")
                    hyperlinks.append(target["Target"])
                elif node.get(w("anchor")):
                    hyperlinks.append("#" + node.get(w("anchor")))
        numbered = self.numbering(properties)
        outline = properties.get("outline", 9)
        label = "section_header" if outline < 9 else "list_item" if numbered else "text"
        if update_heading and label == "section_header" and value.strip():
            self.heading = self.heading[:outline] + [value.strip()]
        block = {"text": value, "label": label, "section": " / ".join(self.heading) or "正文"}
        block.update(rich_metadata)
        if label == "section_header":
            block["heading_level"] = outline + 1
        if numbered:
            block.update(numbered)
        if hyperlinks:
            block["hyperlinks"] = hyperlinks
        if ident:
            block["style_id"] = ident
        return block

    def units(self, element, body_node, reference=None):
        """Retain complete inner paragraphs/tables inside content controls and parts."""
        self.check_features(element)
        reference = reference or f"docx:body:{body_node}"
        previous = self.rich.anchor
        self.rich.anchor = reference
        try:
            if element.tag == w("p"):
                result = [self.paragraph(element)]
            elif element.tag == w("tbl"):
                first_asset, first_table = len(self.rich.assets), len(self.rich.nested_tables)
                result = [_table(element, self, body_node, reference)]
                if len(self.rich.assets) > first_asset:
                    result[0]["images"] = deepcopy(self.rich.assets[first_asset:])
                if len(self.rich.nested_tables) > first_table:
                    result[0]["nested_tables"] = deepcopy(self.rich.nested_tables[first_table:])
            elif element.tag == w("sectPr"):
                result = []
                for relation in element:
                    if relation.tag not in {w("headerReference"), w("footerReference")}:
                        continue
                    kind = "header" if relation.tag == w("headerReference") else "footer"
                    _, part = self.rich.relation(relation.get(f"{{{R}}}id"), kind)
                    if not part:
                        raise ValueError("DOCX_INVALID_DEPENDENCY: external header/footer")
                    if part in self.rich.seen:
                        continue
                    self.rich.seen.add(part)
                    root = _metadata(self.rich.archive, part, self.limits["docx_max_metadata_bytes"])
                    old_part, old_heading = self.rich.part, list(self.heading)
                    self.rich.part, self.heading = part, ["页眉" if kind == "header" else "页脚"]
                    try:
                        for index, child in enumerate(root):
                            for block in self.units(child, body_node, f"docx:part:{part}/node:{index}"):
                                block.update(source_part=part, part_kind=kind)
                                result.append(block)
                    finally:
                        self.rich.part, self.heading = old_part, old_heading
            elif element.tag in {w("bookmarkStart"), w("bookmarkEnd")}:
                if len(element) or (element.text or "").strip():
                    raise ValueError("DOCX_INVALID_MARKER: bookmark marker contains content")
                result = []
            elif element.tag in {w("sdt"), w("sdtContent"), w("customXml"), w("ins"), w("moveTo")}:
                root = element.find(w("sdtContent")) if element.tag == w("sdt") else element
                result = []
                for index, child in enumerate(root if root is not None else []):
                    result.extend(self.units(child, body_node, reference + f"/node:{index}"))
            elif element.tag in {w("del"), w("moveFrom")}:
                self.rich.notice("DOCX_REVISIONS_FINAL_VIEW: deleted and moved-from content excluded")
                result = []
            else:
                self.unsupported("BODY_NODE")
                result = [{"label": "text", "text": self.rich.text(element), "section": " / ".join(self.heading) or "正文"}]
            for index, block in enumerate(result):
                block.setdefault("node_ref", reference if len(result) == 1 else reference + f"/unit:{index}")
            if element.tag not in {w("p"), w("tbl")}:
                return result
            expanded = []
            for block in result:
                expanded.append(block)
                for asset in block.get("images", []):
                    text = asset.get("text") or asset.get("alt_text") or "[图片资源：" + str(asset.get("part")) + "]"
                    expanded.append({"text":text, "label":"text", "section":block["section"],
                                     "content_kind":"image_ocr" if asset.get("text") else "image_resource",
                                     "images":[deepcopy(asset)], "node_ref":block["node_ref"]+"/image:"+asset.get("id", "external")})
            return expanded
        finally:
            self.rich.anchor = previous


def _format_number(value, form):
    if form == "chineseCounting":
        if not 0 <= value <= 9999:
            raise ValueError("DOCX_UNSUPPORTED_NUMBER_FORMAT_RANGE: chineseCounting supports 0..9999")
        if value == 0:
            return "零"
        digits, parts, zero = "零一二三四五六七八九", [], False
        for amount, unit in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
            digit, value = divmod(value, amount)
            if digit:
                if zero:
                    parts.append("零")
                if not (amount == 10 and digit == 1 and not parts):
                    parts.append(digits[digit])
                parts.append(unit)
                zero = False
            elif parts and value:
                zero = True
        return "".join(parts)
    if form in {"lowerLetter", "upperLetter"} and value > 0:
        result = ""
        while value:
            value, digit = divmod(value - 1, 26)
            result = chr(97 + digit) + result
        return result.upper() if form == "upperLetter" else result
    if form in {"lowerRoman", "upperRoman"} and 0 < value < 4000:
        result = ""
        for amount, numeral in ((1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),
                                (50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")):
            count, value = divmod(value, amount)
            result += numeral * count
        return result.lower() if form == "lowerRoman" else result
    return str(value)


def _cell_paragraphs(cell, context, reference):
    values = []
    def walk(parent, path):
        for index, child in enumerate(parent):
            ref = path + f"/node:{index}"
            if child.tag == w("p"):
                previous = context.rich.anchor
                context.rich.anchor = ref
                try:
                    block = context.paragraph(child, update_heading=False)
                    values.append((block.get("marker", "") + " " if block.get("marker") else "") + block["text"])
                finally:
                    context.rich.anchor = previous
            elif child.tag == w("tbl"):
                nested = _table(child, context, 0, ref)
                context.rich.nested_tables.append(nested)
                values.append(nested["text"])
            elif child.tag in {w("sdt"), w("sdtContent"), w("customXml"), w("ins"), w("moveTo")}:
                walk(child.find(w("sdtContent")) if child.tag == w("sdt") else child, ref)
    walk(cell, reference)
    return "\n".join(values)


def _wrapped_children(element, target):
    for child in element:
        if child.tag == target:
            yield child
        elif child.tag in {w("sdt"), w("sdtContent"), w("customXml"), w("ins"), w("moveTo")}:
            root = child.find(w("sdtContent")) if child.tag == w("sdt") else child
            if root is not None:
                yield from _wrapped_children(root, target)


def _table(element, context, body_node, reference=None):
    reference = reference or f"docx:body:{body_node}"
    rows = list(_wrapped_children(element, w("tr")))
    grid = element.find(w("tblGrid"))
    columns = len(grid.findall(w("gridCol"))) if grid is not None else 0
    parsed_rows = []
    for row_index, row in enumerate(rows):
        properties = row.find(w("trPr"))
        before = _int(_val(properties.find(w("gridBefore")), "0"), "GRID_BEFORE", 0) if properties is not None else 0
        after = _int(_val(properties.find(w("gridAfter")), "0"), "GRID_AFTER", 0) if properties is not None else 0
        header = properties is not None and properties.find(w("tblHeader")) is not None and _val(properties.find(w("tblHeader")), "true") not in {"false", "0", "off"}
        cursor = before
        items = []
        for cell_index, cell in enumerate(_wrapped_children(row, w("tc"))):
            p = cell.find(w("tcPr"))
            span = _int(_val(p.find(w("gridSpan")), "1"), "GRID_SPAN", 1) if p is not None else 1
            vertical = p.find(w("vMerge")) if p is not None else None
            horizontal = p.find(w("hMerge")) if p is not None else None
            vm = _val(vertical, "continue") if vertical is not None else None
            hm = _val(horizontal, "continue") if horizontal is not None else None
            if vm not in {None, "continue", "restart"} or hm not in {None, "continue", "restart"}:
                raise ValueError("DOCX_INVALID_TABLE: invalid merge mode")
            cell_ref = f"{reference}/row:{row_index}/cell:{cell_index}"
            item = {"start": cursor, "end": cursor + span, "text": _cell_paragraphs(cell, context, cell_ref),
                    "vm": vm, "hm": hm, "refs": [cell_ref]}
            if hm == "continue":
                if not items or items[-1]["hm"] != "restart" or items[-1]["vm"] != vm:
                    raise ValueError("DOCX_INVALID_TABLE: orphan/incompatible horizontal continuation")
                items[-1]["end"] = item["end"]
                if item["text"].strip():
                    items[-1]["text"] += "\n" + item["text"]
                items[-1]["refs"].extend(item["refs"])
            else:
                items.append(item)
            cursor += span
        width = cursor + after
        if columns and width != columns:
            raise ValueError("DOCX_INVALID_TABLE: row width disagrees with table grid")
        columns = max(columns, width)
        parsed_rows.append((items, header))
    if not rows or columns < 1 or len(rows) * columns > context.limits["docx_max_table_cells"]:
        raise ValueError("DOCX_TABLE_LIMIT: empty or oversized table grid")
    cells, active = [], {}
    for row_index, (items, header) in enumerate(parsed_rows):
        next_active = {}
        for item in items:
            start, end = item["start"], item["end"]
            if item["vm"] == "continue":
                anchor = active.get(start)
                if anchor is None or any(active.get(col) is not anchor for col in range(start, end)) or \
                        anchor["start_col_offset_idx"] != start or anchor["end_col_offset_idx"] != end:
                    raise ValueError("DOCX_INVALID_TABLE: orphan/misaligned vertical continuation")
                if item["text"].strip():
                    anchor["text"] += "\n" + item["text"]
                if anchor["column_header"] != header:
                    context.rich.notice("DOCX_TABLE_HEADER_SPAN: merged cell crosses header and body rows")
                anchor["end_row_offset_idx"] = row_index + 1
                anchor["row_span"] = row_index + 1 - anchor["start_row_offset_idx"]
                anchor["source_refs"].extend(item["refs"])
                cell = anchor
            else:
                cell = {"start_row_offset_idx": row_index, "end_row_offset_idx": row_index + 1,
                        "start_col_offset_idx": start, "end_col_offset_idx": end,
                        "row_span": 1, "col_span": end - start, "text": item["text"],
                        "column_header": header, "row_header": False, "bbox": None,
                        "source_ref": item["refs"][0], "source_refs": item["refs"]}
                cells.append(cell)
            if item["vm"] in {"continue", "restart"}:
                for column in range(start, end):
                    next_active[column] = cell
        active = next_active
    # A readable text projection; structured row/column spans remain canonical.
    lines = []
    for row in range(len(rows)):
        values = [""] * columns
        for cell in cells:
            if cell["start_row_offset_idx"] == row:
                values[cell["start_col_offset_idx"]] = cell["text"].replace("|", "\\|").replace("\n", "<br>")
        lines.append("| " + " | ".join(values) + " |")
        if row == 0:
            lines.append("| " + " | ".join("---" for _ in values) + " |")
    block = {"label": "table", "text": "\n".join(lines), "section": " / ".join(context.heading) or "正文",
            "table_id": reference,
            "table_data": {"num_rows": len(rows), "num_cols": columns, "table_cells": cells}}
    properties = element.find(w("tblPr"))
    if properties is not None:
        title = _val(properties.find(w("tblCaption")))
        if title:
            block["table_caption"] = title
    return block


def _resume(settings, contract, source_hash):
    state = settings.get("resume_state") or settings.get("docx_resume_state") or {}
    if not state:
        if settings.get("resume_batches", 0) or settings.get("resume_pages", 0):
            raise ValueError("DOCX_CHECKPOINT_INVALID: node checkpoint required for resume")
        return {"nextBodyNode": 0, "nextBatch": 0, "heading": [], "cumulativeChars": 0,
                "blocksEmitted": 0, "numberingCounters": {}, "warnings": []}
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION or state.get("parser") != PARSER:
        raise ValueError("DOCX_CHECKPOINT_INVALID: parser/version mismatch")
    if state.get("sourceSha256") != source_hash or state.get("contract") != contract:
        raise ValueError("DOCX_CHECKPOINT_INVALID: file or parser parameters changed")
    for name in ("nextBodyNode", "nextBatch", "cumulativeChars", "blocksEmitted"):
        _int(state.get(name), name.upper(), 0, contract["max_chars"] if name == "cumulativeChars" else contract["docx_max_body_nodes"])
    heading = state.get("heading")
    if not isinstance(heading, list) or len(heading) > 10 or any(not isinstance(v, str) for v in heading) or sum(map(len, heading)) > 20000:
        raise ValueError("DOCX_CHECKPOINT_INVALID: heading state")
    counters = state.get("numberingCounters")
    if not isinstance(counters, dict) or len(counters) > 10000:
        raise ValueError("DOCX_CHECKPOINT_INVALID: numbering state")
    for ident, levels in counters.items():
        if not isinstance(ident, str) or not isinstance(levels, dict):
            raise ValueError("DOCX_CHECKPOINT_INVALID: numbering state")
        for level, value in levels.items():
            _int(level, "LIST_LEVEL", 0, 8)
            _int(value, "LIST_COUNTER", 0)
    warnings = state.get("warnings", [])
    if not isinstance(warnings, list) or len(warnings) > 64 or any(not isinstance(v, str) or len(v) > 512 for v in warnings):
        raise ValueError("DOCX_CHECKPOINT_INVALID: warning state")
    for key, maximum in (("richReferences", 4096), ("notices", 64), ("fieldStack",64)):
        values = state.get(key, [])
        if not isinstance(values, list) or len(values) > maximum or any(not isinstance(v,str) or len(v)>512 for v in values):
            raise ValueError("DOCX_CHECKPOINT_INVALID: rich content state")
    return deepcopy(state)


def _checkpoint(context, source_hash, contract, node, batch, chars, blocks):
    state = {"version": STATE_VERSION, "parser": PARSER, "parseUnit": PARSE_UNIT,
            "contract": deepcopy(contract), "sourceSha256": source_hash,
            "nextBodyNode": node, "nextBatch": batch, "heading": list(context.heading),
            "cumulativeChars": chars, "blocksEmitted": blocks,
            "numberingCounters": deepcopy(context.counters), "warnings": list(context.warnings),
            "richReferences": sorted(context.rich.seen), "notices": list(context.rich.notices),
            "fieldStack": list(context.rich.field_stack)}
    if len(json.dumps(state, ensure_ascii=False).encode("utf-8")) > 256 * 1024:
        raise ValueError("DOCX_CHECKPOINT_LIMIT: state exceeds resume protocol limit")
    return state


def _batch(blocks, context, source_hash, contract, start, end, batch, chars, total_blocks, name, boundary=None):
    from docling_core.types.doc import DoclingDocument
    from chunking_document import attach_chunking_document
    state = _checkpoint(context, source_hash, contract, end, batch + 1, chars, total_blocks)
    for block in blocks:
        block["batch_index"] = batch
        if not block.get("source_fragments"):
            block["source_fragments"] = [deepcopy(block)]
        for fragment in block["source_fragments"]:
            fragment.setdefault("batch_index",batch)
    event = {"type": "batch", "parser": PARSER, "parseUnit": PARSE_UNIT, "batchIndex": batch,
             "startNode": start, "endNode": end, "startPage": 0, "endPage": 0, "totalPages": 0,
             "blocks": blocks, "headingState": list(context.heading), "docxResumeState": state, "resumeState": state,
             "partial": bool(context.warnings), "warnings": list(context.warnings) + list(context.rich.notices) + [BASE_WARNING],
             "structureProvider": "OOXML_NATIVE", "doclingDocument": DoclingDocument(name=name).export_to_dict()}
    if boundary is not None:
        event["docxBoundary"] = boundary
    attach_chunking_document(event)
    # This is the OOXML parser's generated structural view, not Docling inference.
    event["doclingDocument"] = deepcopy(event["chunkingDocument"])
    if len(json.dumps(event, ensure_ascii=False).encode("utf-8")) > 8 * 1024 * 1024 - 1024:
        raise ValueError("DOCX_BATCH_OUTPUT_LIMIT: complete batch exceeds streaming protocol limit")
    return event


def iter_docx_batches(source, settings):
    """Yield metadata, bounded batches and complete; raise ValueError on failure."""
    path = Path(source)
    limits = _limits(settings)
    from docx_batch_boundary import BoundaryPolicy
    policy = BoundaryPolicy(settings,limits)
    source_hash = _source_hash(path, limits["max_bytes"])
    contract = {"rules": "docx-stream-v2-rich", **limits, "ocr_enabled": settings.get("ocr_enabled", False),
                "ocr_languages": settings.get("ocr_languages", ["chi_sim", "eng"])}
    if policy.enabled:
        contract["batchBoundary"] = policy.contract()
    state = _resume(settings, contract, source_hash)
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("DOCX_INVALID_ZIP: cannot open Word archive") from error
    with archive:
        _validate_zip(archive, limits)
        context = _Context(archive, limits, state, settings)
        yield {"type": "metadata", "parser": PARSER, "parseUnit": PARSE_UNIT, "totalPages": 0,
               "sourceSha256": source_hash, "resumedFromNode": state["nextBodyNode"]}
        batch_index = state["nextBatch"]
        cursor, batch_start = 0, state["nextBodyNode"]
        chars, emitted = state["cumulativeChars"], state["blocksEmitted"]
        blocks, batch_chars, stack = [], 0, []
        body_seen = False
        node_bytes_start = 0
        with archive.open("word/document.xml") as stream:
            reader = _SafeXMLReader(stream, limits["docx_max_xml_bytes"])
            try:
                for kind, element in ET.iterparse(reader, events=("start", "end")):
                    if kind == "start":
                        stack.append(element)
                        if len(stack) == 1 and element.tag != w("document"):
                            raise ValueError("DOCX_INVALID_XML: document root")
                        if len(stack) == 2 and element.tag == w("body"):
                            if body_seen:
                                raise ValueError("DOCX_INVALID_XML: duplicate body")
                            body_seen = True
                        if len(stack) == 3 and stack[1].tag == w("body"):
                            node_bytes_start = max(0, reader.count - 16384)
                            reader.node_start = node_bytes_start
                            reader.node_limit = limits["docx_max_node_xml_bytes"]
                        if len(stack) > 128:
                            raise ValueError("DOCX_XML_LIMIT: excessive XML depth")
                        if len(stack) >= 3 and reader.count - node_bytes_start > limits["docx_max_node_xml_bytes"] + 16384:
                            raise ValueError("DOCX_NODE_XML_LIMIT: complete node exceeds hard limit")
                        continue
                    if len(stack) == 3 and stack[1].tag == w("body"):
                        if cursor >= limits["docx_max_body_nodes"]:
                            raise ValueError("DOCX_NODE_LIMIT: too many body nodes")
                        if len(ET.tostring(element, encoding="utf-8")) > limits["docx_max_node_xml_bytes"]:
                            raise ValueError("DOCX_NODE_XML_LIMIT: complete node exceeds hard limit")
                        if cursor >= state["nextBodyNode"]:
                            context.rich.assets, context.rich.nested_tables, context.rich.notes = [], [], []
                            for block in context.units(element, cursor):
                                if not block["text"].strip():
                                    continue
                                if len(block["text"]) > limits["docx_max_node_chars"]:
                                    raise ValueError("DOCX_NODE_CHAR_LIMIT: paragraph/table exceeds hard limit")
                                block.update(ref=block["node_ref"],
                                             body_node=cursor, parse_unit=PARSE_UNIT, pages=[])
                                at_boundary=cursor-batch_start>=limits["docx_batch_nodes"] or batch_chars>=limits["docx_batch_chars"]
                                policy.add(blocks,block,at_boundary)
                                chars += len(block["text"])
                                batch_chars += len(block["text"])
                                emitted += 1
                                if chars > limits["max_chars"]:
                                    raise ValueError("DOCX_TEXT_LIMIT: document exceeds text capacity")
                        cursor += 1
                        element.clear()
                        stack[1].remove(element)
                        reader.node_start = None
                        if cursor > batch_start and (cursor - batch_start >= limits["docx_batch_nodes"] or batch_chars >= limits["docx_batch_chars"]):
                            if policy.extend(blocks,cursor-batch_start,batch_chars):
                                stack.pop()
                                continue
                            yield _batch(blocks, context, source_hash, contract, batch_start, cursor, batch_index, chars, emitted, path.stem,
                                         policy.trace(cursor-batch_start) if policy.enabled else None)
                            batch_index += 1
                            batch_start, blocks, batch_chars = cursor, [], 0
                    stack.pop()
            except ET.ParseError as error:
                raise ValueError("DOCX_INVALID_XML: document.xml") from error
        if not body_seen or cursor < state["nextBodyNode"]:
            raise ValueError("DOCX_CHECKPOINT_INVALID: missing body or resume node beyond document")
        if context.rich.field_stack:
            raise ValueError("DOCX_INVALID_FIELD: unclosed field at end of document")
        if cursor > batch_start:
            yield _batch(blocks, context, source_hash, contract, batch_start, cursor, batch_index, chars, emitted, path.stem,
                         policy.trace(cursor-batch_start) if policy.enabled else None)
            batch_index += 1
        final = _checkpoint(context, source_hash, contract, cursor, batch_index, chars, emitted)
        yield {"type": "complete", "parser": PARSER, "parseUnit": PARSE_UNIT, "totalPages": 0,
               "totalBodyNodes": cursor, "endNode": cursor, "docxResumeState": final, "resumeState": final,
               "partial": bool(context.warnings), "warnings": list(context.warnings) + list(context.rich.notices) + [BASE_WARNING]}


def convert_docx_stream(source, settings, output):
    for event in iter_docx_batches(source, settings):
        output.put(event)
