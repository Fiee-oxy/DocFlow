"""Explainable native profiling and bounded adaptive routing. No chat model calls.

Only immutable, ordered page batches are published. Existing converters remain
the owners of extraction; this module owns selection, quality and audit data.
"""
import copy
import hashlib
import re
import time
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET

REVISION = "adaptive-parser-v1"


@dataclass(frozen=True)
class ParserCapability:
    name: str
    formats: tuple
    layout: bool
    tables: bool
    ocr: bool
    checkpoint: str


CAPABILITIES = (
    ParserCapability("FAST", ("pdf",), False, False, False, "PAGE"),
    ParserCapability("DOCLING", ("pdf", "docx"), True, True, True, "PAGE_OR_DOCUMENT"),
    ParserCapability("DOCX_STREAM", ("docx",), True, True, True, "BODY_NODE"),
)


def text_signals(text):
    compact = re.sub(r"\s", "", text)
    bad = sum(c == "\ufffd" or (ord(c) < 32 and c not in "\n\r\t") for c in text)
    return {"characters": len(compact), "garblingRatio": bad / max(1, len(text))}


def route_page(page, mode="AUTO"):
    if mode == "HIGH_ACCURACY":
        return "DOCLING", ["EXPLICIT_HIGH_ACCURACY"]
    reasons = []
    if page["characters"] < 40:
        reasons.append("SPARSE_NATIVE_TEXT_OCR_OR_BLANK")
    if page["garblingRatio"] > .01:
        reasons.append("NATIVE_GARBLING")
    if page.get("layoutAmbiguous"):
        reasons.append("READING_ORDER_UNCERTAIN")
    if page.get("tableSuspected"):
        reasons.append("POSSIBLE_TABLE")
    if page.get("imageCount", 0):
        reasons.append("EMBEDDED_IMAGE_REQUIRES_LAYOUT")
    return ("DOCLING", reasons) if reasons else ("FAST", ["NATIVE_TEXT_SIMPLE_HEURISTIC"])


def profile_pdf(source, settings):
    import pypdfium2 as pdfium
    from contextlib import closing
    from fast_pdf import native_units
    started = time.monotonic()
    pages = []
    with pdfium.PdfDocument(source) as document:
        total = len(document)
        if total > settings["max_pages"]:
            raise ValueError("PDF page limit exceeded")
        indices = settings.get("profile_page_numbers", range(1, total + 1))
        for number in indices:
            if settings.get("profile_budget_ms") is not None and (time.monotonic()-started)*1000 >= settings["profile_budget_ms"]:
                break
            if not 1 <= number <= total:
                raise ValueError("Invalid prescan page")
            index = number - 1
            with closing(document[index]) as page, closing(page.get_textpage()) as textpage:
                text = textpage.get_text_bounded()
                signals = text_signals(text)
                native = native_units(page, textpage, text, index + 1, "") if text.strip() else None
                # PDFium object type 3 is an image. This is a count, not area coverage.
                images = sum(1 for _ in page.get_objects(filter=[3]))
                lines = text.splitlines()
                table = sum(bool(re.search(r"\S\s{3,}\S", line)) for line in lines) >= 3
                pages.append({"page": index + 1, **signals, "imageCount": images,
                              "layoutAmbiguous": bool(text.strip()) and native is None,
                              "tableSuspected": table})
    return {"revision": REVISION, "format": "pdf", "pages": pages, "totalPages": total,
            "method": "PDFIUM_NATIVE_RECTANGLES_AND_WHITESPACE_HEURISTICS",
            "uncertainty": "Not a calibrated layout classifier; no reliable table/figure completeness score",
            "elapsedMs": round((time.monotonic() - started) * 1000, 3)}


def profile_docx(source, settings):
    started = time.monotonic()
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        if sum(m.file_size for m in members) > settings.get("docx_max_xml_bytes", 134217728):
            raise ValueError("DOCX expanded archive limit exceeded")
        xml = archive.read("word/document.xml")
        if len(xml) > settings.get("docx_max_xml_bytes", 134217728):
            raise ValueError("DOCX XML limit exceeded")
        # No external entities or remote relationships are resolved.
        if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
            raise ValueError("DOCX entities are not accepted")
        root = ET.fromstring(xml)
        tags = [node.tag.rsplit("}", 1)[-1] for node in root.iter()]
        complex_tags = sorted(set(tags) & {"drawing", "pict", "object", "altChunk", "oMath", "oMathPara"})
        complex_media = [m.filename for m in members if m.filename.startswith("word/media/") and
                         not m.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"))]
    return {"revision": REVISION, "format": "docx", "paragraphs": tags.count("p"),
            "tables": tags.count("tbl"), "complexObjects": complex_tags, "complexMedia": complex_media,
            "parser": "DOCLING" if complex_tags or complex_media else "DOCX_STREAM",
            "method": "BOUNDED_OOXML_TAG_PREFLIGHT", "uncertainty": "No rendered layout verification",
            "elapsedMs": round((time.monotonic() - started) * 1000, 3)}


def quality(batch, expected=None):
    blocks = batch.get("blocks", [])
    text = "\n".join(b.get("text", "") for b in blocks)
    signals = text_signals(text)
    reasons = []
    if signals["garblingRatio"] > .01:
        reasons.append("GARBLED_TEXT")
    expected_chars = (expected or {}).get("characters", 0)
    coverage = signals["characters"] / expected_chars if expected_chars else None
    if expected_chars >= 40 and coverage < .8:
        reasons.append("NATIVE_TEXT_LOSS")
    if not text.strip():
        reasons.append("NO_EXTRACTED_TEXT_REVIEW_REQUIRED")
    tables = [b for b in blocks if b.get("label") == "table"]
    return {"revision": REVISION, **signals, "nativeCharacterRatio": coverage,
            "status": "REVIEW_REQUIRED" if reasons else "HEURISTIC_PASS", "reasons": reasons,
            "readingOrder": "UNVERIFIED", "tableStructure": "PRESENT_UNVERIFIED" if tables else "NOT_OBSERVED",
            "structure": "HEURISTIC_ONLY", "calibratedAccuracy": None}


class Capture:
    def __init__(self):
        self.events = []

    def put(self, event):
        self.events.append(event)


def extract(source, settings, parser, start=None, end=None, session=None):
    """Use the original converters, without recursively invoking AUTO."""
    config = copy.deepcopy(settings)
    config["parse_mode"] = parser
    captured = Capture()
    if parser == "FAST":
        # Native only here: adaptive routing owns the single bounded OCR upgrade.
        config.update(ocr_enabled=False, resume_pages=start - 1, fast_batch_pages=1,
                      resume_state={"adaptiveHeading": True})
        # The adapter below stops conversion after one selected page.
        from fast_pdf import convert_fast_pdf

        class OnePage(Capture):
            def put(self, event):
                super().put(event)
                if event["type"] == "batch":
                    raise PageDone()

        captured = OnePage()
        try:
            convert_fast_pdf(source, config, captured)
        except PageDone:
            pass
    elif parser == "DOCX_STREAM":
        from docx_stream import convert_docx_stream
        convert_docx_stream(source, config, captured)
    else:
        from ocr_config import create_converter
        from normalization import normalize
        from docling.datamodel.base_models import ConversionStatus
        kwargs = {"max_num_pages": settings["max_pages"], "max_file_size": settings["max_bytes"]}
        if start:
            kwargs["page_range"] = (start, end)
        session = session if session is not None else {}
        converter = session.get("converter")
        if converter is None:
            converter = session["converter"] = create_converter(config)
        result = converter.convert(source, **kwargs)
        if result.status != ConversionStatus.SUCCESS:
            raise RuntimeError("Docling conversion incomplete")
        heading = settings.get("heading", "").split(" / ") if settings.get("heading") else []
        parsed = normalize(result.document, heading, ocr_enabled=config.get("ocr_enabled", False))
        captured.put({"type": "batch", **parsed, "doclingDocument": result.document.export_to_dict()})
    return captured.events


class PageDone(Exception):
    pass


def convert_adaptive(source, settings, output):
    mode = settings["parse_mode"]
    session = {}
    digest = hashlib.sha256()
    with open(source, "rb") as stream:
        for data in iter(lambda: stream.read(1048576), b""):
            digest.update(data)
    if source.lower().endswith(".docx"):
        profile = profile_docx(source, settings)
        # Publish the native protocol only through the existing explicit mode.
        # AUTO Word currently uses Docling: a mid-stream native fallback could
        # otherwise mix BODY_NODE and document checkpoints after publication.
        parser = "DOCLING"
        events = extract(source, settings, parser, session=session)
        total = 0
        output.put({"type": "metadata", "totalPages": total, "profile": profile})
        for event in events:
            if event["type"] == "batch":
                report = quality(event)
                if report["reasons"]:
                    output.put({"type": "error", "retryable": False, "failureType": "PARSER_QUALITY_REVIEW",
                                "message": "Word extraction needs quality review", "qualityReport": report})
                    return
                if sum(len(b.get("text", "")) for b in event.get("blocks", [])) > settings["max_chars"]:
                    raise ValueError("Parsed text limit exceeded")
                event.update(startPage=0, endPage=0, totalPages=0, parser=parser,
                             parserAudit={"revision": REVISION, "sourceSha256": digest.hexdigest(),
                                          "profile": profile, "reason": "DOCX_DOCUMENT_CHECKPOINT_COMPATIBILITY"},
                             qualityReport=report)
                output.put(event)
        output.put({"type": "complete", "totalPages": 0})
        return
    if settings.get("prescan_enabled", False):
        from prescan import prescan_document
        profile = prescan_document(source, settings)
    else:
        profile = profile_pdf(source, settings)
    total = profile.get("totalPages", len(profile["pages"]))
    if settings.get("resume_pages", 0) > total:
        raise ValueError("Checkpoint exceeds PDF pages")
    output.put({"type": "metadata", "totalPages": total, "profile": profile})
    chars = 0
    scanned = {p["page"]: p for p in profile["pages"]}
    for number in range(settings.get("resume_pages", 0)+1, total+1):
        # An unscanned page is unknown, never assumed simple from other samples.
        page = scanned.get(number)
        if page is None:
            page = profile_pdf(source, settings | {"profile_page_numbers": [number]})["pages"][0]
        start = time.monotonic()
        parser, reasons = route_page(page, mode)
        attempt = 0
        events = extract(source, settings, parser, page["page"], page["page"], session=session)
        batch = next(e for e in events if e["type"] == "batch")
        report = quality(batch, page)
        if report["reasons"] and parser == "FAST":
            attempt = 1
            reasons += report["reasons"]
            parser = "DOCLING"
            batch = next(e for e in extract(source, settings, parser, page["page"], page["page"], session=session) if e["type"] == "batch")
            report = quality(batch, page)
        # Never publish an unqualified page as fully parsed. Previous batches
        # remain usable, with an explicit terminal failure and resumable cursor.
        if report["reasons"]:
            output.put({"type": "error", "retryable": False, "failureType": "PARSER_QUALITY_REVIEW",
                        "message": f"Page {page['page']} needs review: {report['reasons']}", "qualityReport": report})
            return
        chars += sum(len(b.get("text", "")) for b in batch.get("blocks", []))
        if chars > settings["max_chars"]:
            raise ValueError("Parsed text limit exceeded")
        heading = batch.get("headingState", [])
        settings["heading"] = " / ".join(heading)
        batch.update(startPage=page["page"], endPage=page["page"], totalPages=total, parser=parser,
                     qualityReport=report, parserAudit={"revision": REVISION, "mode": mode,
                     "sourceSha256": digest.hexdigest(), "profile": page, "reasons": reasons,
                     "fallbackAttempts": attempt, "elapsedMs": round((time.monotonic()-start)*1000, 3)})
        output.put(batch)
    output.put({"type": "complete", "totalPages": total})
