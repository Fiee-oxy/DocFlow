"""Native PDF text extraction. No layout/table neural models are loaded."""
import re
from contextlib import closing

HEADING = re.compile(r"^(?:第[零一二三四五六七八九十百千万\d]+[章篇部节]|chapter\s+\d+\b)", re.IGNORECASE)
FAST_WARNING = "快速文本解析：保留 PDF 文字层与物理页码；多栏阅读顺序、表格行列和公式结构未重建，图片文字未识别。"


def native_units(page, textpage, text, number, heading):
    """Use rectangles only when their text matches the complete extraction in order.

    This conservative check avoids inventing paragraph coordinates for columns,
    rotated pages, overlapping rectangles, or PDFium's synthesized characters.
    """
    lines = text.splitlines(keepends=True)
    nonempty = [line for line in lines if line.strip()]
    try:
        if page.get_rotation() != 0:
            return None
        width, height = page.get_size()
        count = textpage.count_rects()
        if count != len(nonempty) or count > 4096:
            return None
        boxes = []
        for i, line in enumerate(nonempty):
            left, bottom, right, top = textpage.get_rect(i)
            extracted = textpage.get_text_bounded(left=left, bottom=bottom, right=right, top=top)
            if re.sub(r"\s", "", extracted) != re.sub(r"\s", "", line):
                return None
            box = {"l": left, "t": height-top, "r": right, "b": height-bottom, "coord_origin": "TOPLEFT"}
            if boxes and box["t"] < boxes[-1]["t"] - max(2, boxes[-1]["b"]-boxes[-1]["t"]):
                return None
            boxes.append(box)
    except (AttributeError, ValueError, RuntimeError):
        return None
    result, group, group_boxes = [], [], []
    position = 0
    list_start = re.compile(r"^(?:[-*•]\s+|(?:\d+|[一二三四五六七八九十]+)[、.)）]\s*|[（(]\d+[）)])")

    def flush(label="text"):
        if not group:
            return
        content = "".join(group).strip()
        bbox = {"l": min(b["l"] for b in group_boxes), "r": max(b["r"] for b in group_boxes),
                "t": min(b["t"] for b in group_boxes), "b": max(b["b"] for b in group_boxes), "coord_origin": "TOPLEFT"}
        result.append({"ref": f"pdf-page-{number}-unit-{len(result)}", "section": heading, "text": content,
                       "pages": [number], "label": label, "bbox": bbox, "page_size": {"width": width, "height": height}})
        group.clear(); group_boxes.clear()

    for line in lines:
        if not line.strip():
            flush()
            continue
        box = boxes[position]; position += 1
        title = len(line.strip()) <= 100 and HEADING.match(line.strip())
        if title:
            flush(); heading = line.strip()
            group.append(line); group_boxes.append(box); flush("section_header")
            continue
        if group:
            previous = group_boxes[-1]
            line_height = max(1, previous["b"]-previous["t"])
            if box["t"]-previous["b"] > line_height * .8 or list_start.match(line.strip()):
                flush()
        group.append(line); group_boxes.append(box)
    flush()
    return result, heading


def convert_fast_pdf(source, settings, output):
    import pypdfium2 as pdfium
    try:
        document = pdfium.PdfDocument(source)
    except pdfium.PdfiumError as error:
        raise ValueError("PDF 无法读取，请确认文件完整且未加密") from error
    with document:
        total = len(document)
        if total > settings["max_pages"] or settings["resume_pages"] > total:
            raise ValueError("PDF 页数超限或解析检查点无效")
        output.put({"type": "metadata", "totalPages": total, "parser": "FAST"})
        chars = 0
        converter = None
        # Do not propagate a previous parser's chapter label over the rest of the book.
        heading = settings.get("heading", "正文") if settings.get("resume_state") else "正文"
        batch_pages = settings.get("fast_batch_pages", 32)
        for start in range(settings["resume_pages"] + 1, total + 1, batch_pages):
            end = min(total, start + batch_pages - 1)
            blocks, missing = [], []
            for number in range(start, end + 1):
                with closing(document[number - 1]) as page:
                    with closing(page.get_textpage()) as textpage:
                        text = textpage.get_text_bounded().replace("\x00", "").strip()
                        native = native_units(page,textpage,text,number,heading) if text and settings.get("_continuity_active",settings.get("continuity_enabled",True)) else None
                # Sparse text (including a scan with only a page number) needs OCR.
                if settings.get("ocr_enabled", False) and len(re.sub(r"\s", "", text)) < 40:
                    from ocr_config import create_converter
                    from normalization import normalize
                    from docling.datamodel.base_models import ConversionStatus
                    # Fast workers share one expensive OCR slot in the Linux container.
                    import fcntl
                    with open("/tmp/docling-fast-ocr.lock", "w") as gate:
                        fcntl.flock(gate, fcntl.LOCK_EX)
                        converter = converter or create_converter(settings)
                        result = converter.convert(source, page_range=(number, number),
                            max_num_pages=settings["max_pages"], max_file_size=settings["max_bytes"])
                    if result.status != ConversionStatus.SUCCESS:
                        raise RuntimeError(f"OCR page {number} did not complete; retry this batch")
                    parsed = normalize(result.document, ocr_enabled=True)
                    for block in parsed["blocks"]:
                        block["ref"] = f"ocr-page-{number}:{block['ref']}"
                        block["pages"] = [number]
                    blocks.extend(parsed["blocks"])
                    text = "\n".join(block["text"] for block in parsed["blocks"])
                    chars += len(text)
                    if chars > settings["max_chars"]:
                        raise ValueError("OCR text exceeds document capacity")
                    if not text:
                        missing.append(number)
                    continue
                chars += len(text)
                if chars > settings["max_chars"]:
                    raise ValueError("文档文本超过容量限制")
                if not text:
                    missing.append(number)
                    continue
                # Only explicit chapter markers establish headings; otherwise retain paragraphs verbatim.
                lines = text.splitlines()
                first = next((line.strip() for line in lines if line.strip()), "")
                if len(first) <= 100 and HEADING.match(first):
                    heading = first
                if native is not None:
                    units, heading = native
                    blocks.extend(units)
                else:
                    blocks.append({"ref": f"pdf-page-{number}", "section": heading,
                                   "text": text, "pages": [number], "label": "text", "layout_ambiguous": True})
            warnings = [FAST_WARNING]
            if settings.get("ocr_enabled", False):
                warnings.append("快速模式仅对文字稀少页面补做 OCR；文字丰富页面中的图片文字可能遗漏，需要时使用 Docling 结构解析。")
            if missing:
                warnings.append("以下物理页没有可提取文字，可能为空白、图片或扫描页，需要人工核对或 OCR：" + ", ".join(map(str, missing)))
            output.put({"type": "batch", "startPage": start, "endPage": end, "totalPages": total,
                        "blocks": blocks, "warnings": warnings, "partial": False, "parser": "FAST", "headingState": [heading]})
        output.put({"type": "complete", "totalPages": total, "parser": "FAST"})
