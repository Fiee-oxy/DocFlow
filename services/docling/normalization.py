"""Preserve reading order and source locations without flattening the whole file."""
import re

def advance_heading(heading, text, level=1):
    """Repair common flat PDF heading levels without inventing semantic headings."""
    text = re.sub(r"\s+", " ", text).strip()
    # A named programme is a new root; numbered subsections belong to that root.
    if re.search(r"\S+专业培养方案", text) and len(text) < 160:
        return [text]
    if re.match(r"^[一二三四五六七八九十百]+[、．.]", text):
        root = heading[:1] if heading and not re.match(r"^[一二三四五六七八九十百]+[、．.]", heading[0]) else []
        return root + [text]
    if re.match(r"^[（(][一二三四五六七八九十百]+[）)]", text):
        return heading[:2] + [text]
    if re.match(r"^毕业要求\s*\d+", text) and len(heading) >= 2:
        return heading[:2] + [text]
    if re.match(r"^\d+[、．.]", text) and len(heading)>=2:
        return heading[:2]+[text]
    if level==1 and heading and re.search(r"\S+专业培养方案",heading[0]):
        return heading[:2]+[text]
    return heading[:max(0, level-1)] + [text]

def annotate_batch(batch, initial_heading=None):
    """Replay already-parsed batches; no layout/OCR inference or LLM call."""
    heading=list(initial_heading or [])
    levels={}
    for block in batch.get("blocks", []):
        if block.get("label") in {"title", "section_header"}:
            heading=advance_heading(heading,block["text"],block.get("heading_level",1))
            levels[block.get("ref")]=len(heading)
        block["section"]=" / ".join(heading) or "正文"
    for text in batch.get("doclingDocument",{}).get("texts",[]):
        if text.get("self_ref") in levels and text.get("label")=="section_header":
            text["level"]=levels[text["self_ref"]]
    return heading


def normalize(document, initial_heading=None, ocr_enabled=False):
    blocks = []
    heading = list(initial_heading or [])
    text_pages = set()
    warnings = []
    for item, _ in document.iterate_items():
        label = getattr(getattr(item, "label", None), "value", "")
        if label in {"page_header", "page_footer"}:
            continue
        if label == "table":
            text = item.export_to_markdown(doc=document)
        else:
            text = getattr(item, "text", "")
        text = (text or "").strip()
        if not text:
            continue
        if label in {"title", "section_header"}:
            level = max(1, int(getattr(item, "level", 1) or 1))
            heading = advance_heading(heading, text, level)
            if label == "section_header":
                item.level = len(heading)
        pages = sorted({p.page_no for p in getattr(item, "prov", [])})
        text_pages.update(pages)
        block = {
            "ref": item.self_ref,
            "section": " / ".join(heading) or "正文",
            "text": text,
            "pages": pages,
            "label": label,
            "heading_level": len(heading) if label in {"title", "section_header"} else None,
        }
        # Retain original geometry and structured cells for conservative boundary decisions.
        provenance = []
        for source in getattr(item, "prov", []):
            if hasattr(source, "model_dump"):
                provenance.append(source.model_dump(mode="json"))
        if provenance:
            block["prov"] = provenance
        if len(pages) == 1 and provenance:
            boxes = [p.get("bbox") for p in provenance if p.get("bbox")]
            page = getattr(document, "pages", {}).get(pages[0])
            size = getattr(page, "size", None)
            if size and boxes:
                block["page_size"] = {"width": size.width, "height": size.height}
                # One item's provenance normally has one box; multiple boxes stay in prov.
                if len(boxes) == 1:
                    block["bbox"] = boxes[0]
        if label == "table":
            data = getattr(item, "data", None)
            if hasattr(data, "model_dump"):
                block["table_data"] = data.model_dump(mode="json")
            caption = getattr(item, "caption_text", None)
            if callable(caption):
                value = caption(doc=document)
                if value:
                    block["caption"] = value
        blocks.append(block)
    missing = sorted(set(getattr(document, "pages", {}).keys()) - text_pages)
    if missing:
        warnings.append("以下物理页没有提取到正文，可能是封面、图片或扫描页：" + ", ".join(map(str, missing)))
    warnings.append("已启用中英文 OCR；识别结果可能有误，复杂公式和图片内容仍需核对。" if ocr_enabled else "当前未启用 OCR；图片中的文字与复杂公式可能未被提取。")
    return {"blocks": blocks, "warnings": warnings, "partial": False, "headingState": heading}
