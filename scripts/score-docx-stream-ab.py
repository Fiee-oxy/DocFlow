"""Score DOCX parser A/B artifacts against independent OOXML, without model calls.

This is structural/text QA, not rendered layout QA or answer accuracy.  It reads
the source ZIP/XML directly rather than treating either parser's output as gold.
Only whitespace is normalized; numbers, punctuation and case remain significant.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

WORKSPACE = Path(__file__).resolve().parents[1]
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NS = {"w": W[1:-1]}
SCORING_REVISION = "ooxml-v2-numbering-projection"


def normalize(text):
    return re.sub(r"\s+", "", str(text or ""))


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def val(node, path, default=None):
    item = node.find(path, NS) if node is not None else None
    return item.get(W + "val", default) if item is not None else default


def visible_text(node):
    """Accepted revisions and cached field results; images/textboxes are separate."""
    result = []

    def visit(item):
        local = item.tag.rsplit("}", 1)[-1]
        if local in {"del", "moveFrom", "instrText", "drawing", "pict", "object", "txbxContent"}:
            return
        if item.tag == W + "t":
            result.append(item.text or "")
        elif item.tag in {W + "tab", W + "ptab"}:
            result.append("\t")
        elif item.tag in {W + "br", W + "cr"}:
            result.append("\n")
        elif item.tag == W + "noBreakHyphen":
            result.append("\u2011")
        elif item.tag == W + "softHyphen":
            result.append("\u00ad")
        else:
            for child in item:
                visit(child)

    visit(node)
    return "".join(result)


def body_nodes(body):
    """Flatten content-control wrappers, while keeping tables as atomic nodes."""
    for child in body:
        if child.tag in {W + "p", W + "tbl"}:
            yield child
        elif child.tag == W + "sdt":
            content = child.find("w:sdtContent", NS)
            if content is not None:
                yield from body_nodes(content)
        elif child.tag in {W + "customXml", W + "ins", W + "moveTo"}:
            yield from body_nodes(child)


def styles_for(root):
    styles = {}
    if root is None:
        return styles
    for node in root.findall("w:style", NS):
        styles[node.get(W + "styleId")] = {
            "name": val(node, "w:name", ""), "base": val(node, "w:basedOn"),
            "outline": val(node, "w:pPr/w:outlineLvl"),
            "num_id": val(node, "w:pPr/w:numPr/w:numId"),
            "num_level": val(node, "w:pPr/w:numPr/w:ilvl")}
    return styles


def style_value(styles, style_id, field):
    seen = set()
    while style_id and style_id not in seen:
        seen.add(style_id)
        style = styles.get(style_id, {})
        if style.get(field) is not None:
            return style[field]
        style_id = style.get("base")
    return None


def paragraph_info(node, source, styles):
    text = visible_text(node)
    style_id = val(node, "w:pPr/w:pStyle")
    outline = val(node, "w:pPr/w:outlineLvl")
    if outline is None:
        outline = style_value(styles, style_id, "outline")
    level = integer(outline, 9) + 1 if outline is not None and integer(outline, 9) < 9 else None
    # Named built-in heading styles are explicit structure even if outline is inherited externally.
    if level is None:
        name = styles.get(style_id, {}).get("name", "")
        match = re.fullmatch(r"(?:heading|标题)\s*([1-9])", name, re.I)
        if match:
            level = int(match[1])
    num_id = val(node, "w:pPr/w:numPr/w:numId")
    num_level = val(node, "w:pPr/w:numPr/w:ilvl")
    if num_id is None:
        num_id = style_value(styles, style_id, "num_id")
    if num_level is None:
        num_level = style_value(styles, style_id, "num_level")
    if num_id == "0":
        num_id = None
    runs = [visible_text(run) for run in node.iter(W + "r")]
    return {"source": source, "text": text, "style_id": style_id, "heading_level": level,
            "num_id": num_id, "num_level": integer(num_level),
            "run_count": len(runs), "nonempty_run_count": sum(bool(t) for t in runs)}


def format_number(number, style):
    if style in {"decimal", "decimalZero"}:
        return f"{number:02}" if style == "decimalZero" else str(number)
    if style in {"lowerLetter", "upperLetter"}:
        result = ""
        while number > 0:
            number, digit = divmod(number - 1, 26)
            result = chr(ord("a") + digit) + result
        return result.upper() if style == "upperLetter" else result
    if style in {"lowerRoman", "upperRoman"}:
        result = ""
        for value, letters in [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
                               (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
                               (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]:
            count, number = divmod(number, value)
            result += letters * count
        return result.lower() if style == "lowerRoman" else result
    return None


def numbering_for(root):
    abstract, numbers = {}, {}
    if root is None:
        return numbers
    for item in root.findall("w:abstractNum", NS):
        abstract[item.get(W + "abstractNumId")] = {
            integer(level.get(W + "ilvl")): {
                "start": integer(val(level, "w:start"), 1),
                "format": val(level, "w:numFmt", "decimal"),
                "text": val(level, "w:lvlText", ""),
                "restart": val(level, "w:lvlRestart")}
            for level in item.findall("w:lvl", NS)}
    for item in root.findall("w:num", NS):
        levels = deepcopy(abstract.get(val(item, "w:abstractNumId"), {}))
        for override in item.findall("w:lvlOverride", NS):
            level = integer(override.get(W + "ilvl"))
            start = val(override, "w:startOverride")
            if start is not None:
                levels.setdefault(level, {})["start"] = integer(start, 1)
            full = override.find("w:lvl", NS)
            if full is not None:
                levels[level] = {"start": integer(val(full, "w:start"), 1),
                                 "format": val(full, "w:numFmt", "decimal"),
                                 "text": val(full, "w:lvlText", ""),
                                 "restart": val(full, "w:lvlRestart")}
            if start is not None:
                levels.setdefault(level, {})["start"] = integer(start, 1)
        numbers[item.get(W + "numId")] = levels
    return numbers


def apply_numbering(paragraphs, numbers, unsupported):
    counters = {}
    for paragraph in paragraphs:
        num_id, level = paragraph["num_id"], paragraph["num_level"]
        if num_id is None:
            continue
        levels = numbers.get(num_id, {})
        definition = levels.get(level)
        if not definition or definition.get("restart") is not None:
            unsupported.append({"feature": "numbering_definition_or_restart", "source": paragraph["source"]})
            paragraph["numbering_status"] = "UNSUPPORTED"
            continue
        values = counters.setdefault(num_id, {})
        values[level] = values.get(level, definition.get("start", 1) - 1) + 1
        for deeper in list(values):
            if deeper > level:
                del values[deeper]
        label = definition.get("text", "")
        if definition.get("format") == "bullet":
            paragraph["numbering_status"] = "BULLET_GLYPH_REQUIRES_FONT_MAPPING"
            paragraph["number_label"] = label
            unsupported.append({"feature": "bullet_font_glyph_mapping", "source": paragraph["source"]})
            continue
        supported = True
        for token in re.findall(r"%([1-9])", label):
            target = int(token) - 1
            parent = levels.get(target, {})
            number = format_number(values.get(target, parent.get("start", 1)), parent.get("format", "decimal"))
            if number is None:
                supported = False
                break
            label = label.replace("%" + token, number)
        paragraph["number_label"] = label
        paragraph["numbering_status"] = "SUPPORTED" if supported else "UNSUPPORTED_FORMAT"
        if not supported:
            unsupported.append({"feature": "numbering_format", "source": paragraph["source"]})


def table_gold(node, source, styles, unsupported):
    if len(list(node.iter(W + "tbl"))) > 1:
        unsupported.append({"feature": "nested_tables", "source": source})
    rows = node.findall("w:tr", NS)
    num_cols = len(node.findall("w:tblGrid/w:gridCol", NS))
    anchors, active, paragraphs, physical_texts = [], {}, [], []
    for row_index, row in enumerate(rows):
        col = integer(val(row, "w:trPr/w:gridBefore"))
        next_active = {}
        header_node = row.find("w:trPr/w:tblHeader", NS)
        header = header_node is not None and header_node.get(W + "val", "1") not in {"0", "false", "off"}
        for cell_index, cell in enumerate(row.findall("w:tc", NS)):
            span = integer(val(cell, "w:tcPr/w:gridSpan"), 1)
            if cell.find("w:tcPr/w:hMerge", NS) is not None:
                unsupported.append({"feature": "legacy_hMerge", "source": source})
            cell_source = f"{source}/row[{row_index}]/cell[{cell_index}]"
            parts = [paragraph_info(p, f"{cell_source}/p[{i}]", styles)
                     for i, p in enumerate(cell.findall("w:p", NS))]
            paragraphs.extend(parts)
            text = "\n".join(p["text"] for p in parts)
            if normalize(text):
                physical_texts.append(text)
            merge = cell.find("w:tcPr/w:vMerge", NS)
            continuing = merge is not None and merge.get(W + "val", "continue") != "restart"
            anchor = active.get(col) if continuing else None
            if continuing and (anchor is None or anchor["end_col_offset_idx"] != col + span):
                unsupported.append({"feature": "invalid_vertical_merge", "source": cell_source})
                anchor = None
            if anchor is None:
                anchor = {"text": text, "start_row_offset_idx": row_index, "end_row_offset_idx": row_index + 1,
                          "start_col_offset_idx": col, "end_col_offset_idx": col + span,
                          "row_span": 1, "col_span": span, "column_header": header, "source": cell_source,
                          "paragraph_sources": [p["source"] for p in parts]}
                anchors.append(anchor)
            else:
                anchor["end_row_offset_idx"] = row_index + 1
                anchor["row_span"] = row_index + 1 - anchor["start_row_offset_idx"]
                if normalize(text):
                    anchor["text"] += "\n" + text
                anchor["paragraph_sources"].extend(p["source"] for p in parts)
            if merge is not None:
                next_active[col] = anchor
            col += span
        num_cols = max(num_cols, col + integer(val(row, "w:trPr/w:gridAfter")))
        active = next_active
    return {"source": source, "num_rows": len(rows), "num_cols": num_cols,
            "cells": anchors, "paragraphs": paragraphs, "physical_texts": physical_texts}


def ooxml_gold(path):
    unsupported = []
    with zipfile.ZipFile(path) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        body = document.find("w:body", NS)
        styles = styles_for(ET.fromstring(archive.read("word/styles.xml"))) if "word/styles.xml" in archive.namelist() else {}
        numbers = numbering_for(ET.fromstring(archive.read("word/numbering.xml"))) if "word/numbering.xml" in archive.namelist() else {}
        paragraphs, tables, nodes, all_paragraphs = [], [], [], []
        for index, item in enumerate(body_nodes(body)):
            source = f"word/document.xml/body[{index}]"
            if item.tag == W + "p":
                paragraph = paragraph_info(item, source, styles)
                paragraphs.append(paragraph)
                all_paragraphs.append(paragraph)
                if normalize(paragraph["text"]):
                    nodes.append({"kind": "paragraph", "source": source, "text": paragraph["text"]})
            else:
                table = table_gold(item, source, styles, unsupported)
                tables.append(table)
                all_paragraphs.extend(table["paragraphs"])
                nodes.append({"kind": "table", "source": source, "table_index": len(tables) - 1})
        apply_numbering(all_paragraphs, numbers, unsupported)
        # Numbering lives outside w:t.  A parser may preserve it as metadata or
        # render it into table cell text.  Compare raw body text independently
        # from whether those generated labels were retained.
        for table in tables:
            by_source = {p["source"]: p for p in table["paragraphs"]}
            for cell in table["cells"]:
                parts = [by_source[source] for source in cell["paragraph_sources"]]
                cell["numbered_paragraphs"] = [p for p in parts if p.get("numbering_status") == "SUPPORTED"]
                cell["text_pattern"] = "".join(
                    ("(?:" + re.escape(normalize(p["number_label"])) + ")?" if p.get("numbering_status") == "SUPPORTED" else "")
                    + re.escape(normalize(p["text"])) for p in parts)
        for tag, feature in [("drawing", "embedded_drawing_or_image"), ("pict", "vml_image_or_textbox"),
                             ("footnoteReference", "footnotes"), ("endnoteReference", "endnotes"),
                             ("sym", "font_encoded_symbol"), ("altChunk", "external_altChunk"),
                             ("del", "tracked_deletion_excluded"), ("ins", "tracked_insertion_accepted")]:
            count = len(list(body.iter(W + tag)))
            if count:
                unsupported.append({"feature": feature, "count": count})
        math_count = sum(item.tag.rsplit("}", 1)[-1] in {"oMath", "oMathPara"} for item in body.iter())
        if math_count:
            unsupported.append({"feature": "equations_not_plain_text", "count": math_count})
        field_count = len(list(body.iter(W + "instrText"))) + len(list(body.iter(W + "fldSimple")))
        if field_count:
            unsupported.append({"feature": "fields_cached_results_only_not_recalculated", "count": field_count})
        if list(body.iter(W + "sdt")):
            unsupported.append({"feature": "content_control_values_only_not_behavior", "count": len(list(body.iter(W + "sdt")))})
        for name in archive.namelist():
            if re.fullmatch(r"word/(?:header|footer)\d+\.xml", name):
                if normalize(visible_text(ET.fromstring(archive.read(name)))):
                    unsupported.append({"feature": "header_footer_outside_body", "part": name})
        info = archive.getinfo("word/document.xml")
        source = {"path": str(path.resolve()), "bytes": path.stat().st_size,
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "document_xml_bytes": info.file_size,
                  "embedded_media_bytes": sum(i.file_size for i in archive.infolist() if i.filename.startswith("word/media/"))}
    heading_stack = []
    for paragraph in paragraphs:
        level = paragraph["heading_level"]
        if level is not None:
            heading_stack = [(n, title) for n, title in heading_stack if n < level]
            heading_stack.append((level, paragraph["text"]))
        paragraph["expected_section"] = [title for _, title in heading_stack]
    nonempty = [p for p in paragraphs if normalize(p["text"])]
    return {"basis": "INDEPENDENT_SOURCE_OOXML_WHITESPACE_ONLY", "source": source,
            "paragraphs": paragraphs, "tables": tables, "ordered_nodes": nodes, "unsupported": unsupported,
            "features": {"body_nodes": len(nodes), "nonempty_paragraphs": len(nonempty),
                         "multi_run_paragraphs": sum(p["nonempty_run_count"] > 1 for p in nonempty),
                         "explicit_headings": sum(p["heading_level"] is not None for p in nonempty),
                         "numbered_paragraphs": sum(p["num_id"] is not None for p in nonempty),
                         "tables": len(tables), "table_rows": sum(t["num_rows"] for t in tables),
                         "table_cells": sum(len(t["cells"]) for t in tables),
                         "merged_cells": sum(c["row_span"] > 1 or c["col_span"] > 1 for t in tables for c in t["cells"]),
                         "body_text_characters": sum(len(normalize(p["text"])) for p in nonempty),
                         "table_text_characters": sum(len(normalize(c["text"])) for t in tables for c in t["cells"])}}


def read_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def rate(matched, total):
    return {"matched": matched, "total": total, "ratio": matched / total if total else None}


def section_matches(expected, actual):
    position = 0
    actual = normalize(actual)
    for title in expected:
        found = actual.find(normalize(title), position)
        if found < 0:
            return False
        position = found + len(normalize(title))
    return True


def observed_tables(events):
    result = []
    block_index = 0
    for event in events:
        raw = event.get("chunkingDocument") or event.get("doclingDocument") or {}
        raw_tables = {t.get("self_ref"): t for t in raw.get("tables", [])}
        for block in event.get("blocks", []):
            index = block_index
            block_index += 1
            if block.get("label") != "table":
                continue
            data = block.get("table_data") or block.get("attributes", {}).get("table") or raw_tables.get(block.get("ref"), {}).get("data")
            result.append({"block": block, "block_index": index, "data": data})
    return result


def cell_signature(cell):
    return tuple(integer(cell.get(k)) for k in ("start_row_offset_idx", "end_row_offset_idx", "start_col_offset_idx", "end_col_offset_idx")) + (normalize(cell.get("text")),)


def project_numbered_cells(expected_cells, actual_cells):
    """Remove only optional labels independently derived from each source cell."""
    by_coordinates = {cell_signature(c)[:4]: c for c in expected_cells}
    result = []
    for actual in actual_cells:
        expected = by_coordinates.get(cell_signature(actual)[:4])
        value = dict(actual)
        if expected is not None and expected.get("text_pattern") is not None and \
                re.fullmatch(expected["text_pattern"], normalize(actual.get("text"))):
            value["text"] = expected["text"]
        result.append(value)
    return result


def score_events(gold, events):
    batches = [e for e in events if e.get("type") == "batch"]
    blocks = [b for e in batches for b in e.get("blocks", [])]
    output = [normalize(b.get("text")) for b in blocks]
    joined = "".join(output)
    paragraphs = [p for p in gold["paragraphs"] if normalize(p["text"])]
    whole, contiguous, ordered, multi_run, heading_ok, section_ok = [], [], [], [], [], []
    cursor = 0
    for paragraph in paragraphs:
        text = normalize(paragraph["text"])
        candidates = [i for i, value in enumerate(output) if text in value]
        kept = text in joined
        whole.append(kept)
        contiguous.append(bool(candidates))
        position = joined.find(text, cursor)
        ordered.append(position >= 0)
        if position >= 0:
            cursor = position + len(text)
        if paragraph["nonempty_run_count"] > 1:
            multi_run.append(bool(candidates))
        if paragraph["heading_level"] is not None:
            heading_ok.append(any(blocks[i].get("label") in {"section_header", "title"} and
                                  integer(blocks[i].get("heading_level"), -1) == paragraph["heading_level"] for i in candidates))
        if paragraph["expected_section"]:
            expected = [normalize(s) for s in paragraph["expected_section"]]
            section_ok.append(any(section_matches(expected, blocks[i].get("section")) for i in candidates))
    issues = [{"source": p["source"], "text": p["text"][:300], "retained": kept,
               "single_block": single, "ordered": sequence}
              for p, kept, single, sequence in zip(paragraphs, whole, contiguous, ordered)
              if not (kept and single and sequence)]
    exact_expected = Counter(normalize(p["text"]) for p in paragraphs)
    exact_output = Counter(value for b, value in zip(blocks, output) if value and b.get("label") != "table")
    duplicate_exact = [{"text": text[:200], "expected": count, "observed_exact_blocks": exact_output[text]}
                       for text, count in exact_expected.items() if exact_output[text] > count]

    observed = observed_tables(batches)
    offsets = [0]
    for text in output:
        offsets.append(offsets[-1] + len(text))
    node_order, node_cursor = [], 0
    for node in gold["ordered_nodes"]:
        if node["kind"] == "paragraph":
            text = normalize(node["text"])
            position = joined.find(text, node_cursor)
            matched = position >= 0
            if matched:
                node_cursor = position + len(text)
        else:
            index = node["table_index"]
            position = observed[index]["block_index"] if index < len(observed) else None
            matched = position is not None and offsets[position] >= node_cursor
            if matched:
                node_cursor = offsets[position + 1]
        node_order.append(matched)
    table_details = []
    cells_matched = cells_total = values_matched = values_total = raw_cells_matched = 0
    table_numbering_matched = table_numbering_total = 0
    for index, table in enumerate(gold["tables"]):
        actual = observed[index]["data"] if index < len(observed) else None
        expected_cells = Counter(cell_signature(c) for c in table["cells"])
        raw_actual = (actual or {}).get("table_cells", [])
        raw_actual_cells = Counter(cell_signature(c) for c in raw_actual)
        projected_actual = project_numbered_cells(table["cells"], raw_actual)
        actual_cells = Counter(cell_signature(c) for c in projected_actual)
        expected_values = Counter(normalize(c["text"]) for c in table["cells"] if normalize(c["text"]))
        actual_values = Counter(normalize(c.get("text")) for c in projected_actual if normalize(c.get("text")))
        matched = sum((expected_cells & actual_cells).values())
        value_matched = sum((expected_values & actual_values).values())
        cells_matched += matched
        raw_matched = sum((expected_cells & raw_actual_cells).values())
        raw_cells_matched += raw_matched
        cells_total += sum(expected_cells.values())
        values_matched += value_matched
        values_total += sum(expected_values.values())
        expected_spans = Counter(cell_signature(c) for c in table["cells"] if c["row_span"] > 1 or c["col_span"] > 1)
        by_coordinates = {cell_signature(c)[:4]: c for c in raw_actual}
        numbering_ok = []
        for expected in table["cells"]:
            text = normalize(by_coordinates.get(cell_signature(expected)[:4], {}).get("text"))
            for paragraph in expected.get("numbered_paragraphs", []):
                numbering_ok.append(normalize(paragraph["number_label"] + paragraph["text"]) in text)
        table_numbering_matched += sum(numbering_ok)
        table_numbering_total += len(numbering_ok)
        table_details.append({"source": table["source"], "table_index": index,
                              "expected_rows": table["num_rows"], "expected_cols": table["num_cols"],
                              "actual_rows": (actual or {}).get("num_rows"), "actual_cols": (actual or {}).get("num_cols"),
                              "dimensions_match": bool(actual) and table["num_rows"] == actual.get("num_rows") and table["num_cols"] == actual.get("num_cols"),
                              "cells_text_and_spans": rate(matched, sum(expected_cells.values())),
                              "raw_exact_cells_text_and_spans": rate(raw_matched, sum(expected_cells.values())),
                              "numbering_labels": rate(sum(numbering_ok), len(numbering_ok)),
                              "cell_values": rate(value_matched, sum(expected_values.values())),
                              "merged_cells": rate(sum((expected_spans & actual_cells).values()), sum(expected_spans.values())),
                              "extra_cells": sum((actual_cells - expected_cells).values()),
                              "missing_cell_examples": list((expected_cells - actual_cells).elements())[:20],
                              "structured_output_available": actual is not None})
    lists = [p for p in paragraphs if p.get("numbering_status") == "SUPPORTED"]
    list_ok, source_list_ok = [], []
    raw_items = []
    for event in batches:
        document = event.get("chunkingDocument") or event.get("doclingDocument") or {}
        raw_items.extend(item for item in document.get("texts", []) if item.get("marker"))
    for paragraph in lists:
        text, label = normalize(paragraph["text"]), normalize(paragraph["number_label"])
        kept = False
        for block in blocks:
            body = normalize(block.get("text"))
            position = body.find(text)
            metadata = block.get("numbering") or block.get("attributes", {}).get("numbering") or {}
            marker = normalize(block.get("marker") or metadata.get("label") or metadata.get("marker"))
            if position >= 0 and (body[:position].endswith(label) or marker == label):
                kept = True
                break
        list_ok.append(kept)
        raw_kept = any(normalize(item.get("marker")) == label and
                       (text in normalize(item.get("text")) or
                        (len(normalize(item.get("text"))) >= min(12, len(text)) and text.startswith(normalize(item.get("text")))))
                       for item in raw_items)
        source_list_ok.append(kept or raw_kept)
    return {"completed": any(e.get("type") == "complete" for e in events),
            "partial": any(bool(e.get("partial")) for e in events),
            "warnings": list(dict.fromkeys(str(w) for e in events for w in e.get("warnings", []))),
            "errors": [e for e in events if e.get("type") == "error"],
            "batch_count": len(batches), "block_count": len(blocks),
            "paragraph_text_retained": rate(sum(whole), len(whole)),
            "paragraph_in_single_block": rate(sum(contiguous), len(contiguous)),
            "paragraph_order_preserved": rate(sum(ordered), len(ordered)),
            "body_node_order_preserved": rate(sum(node_order), len(node_order)),
            "multi_run_paragraph_complete": rate(sum(multi_run), len(multi_run)),
            "explicit_heading_level": rate(sum(heading_ok), len(heading_ok)),
            "explicit_section_path": rate(sum(section_ok), len(section_ok)),
            "supported_numbering_label": rate(sum(source_list_ok), len(source_list_ok)),
            "normalized_block_numbering_label": rate(sum(list_ok), len(list_ok)),
            "table_numbering_label": rate(table_numbering_matched, table_numbering_total),
            "table_count": {"expected": len(gold["tables"]), "observed": len(observed)},
            "table_cells_text_and_spans": rate(cells_matched, cells_total),
            "table_raw_exact_cells_text_and_spans": rate(raw_cells_matched, cells_total),
            "table_values": rate(values_matched, values_total), "tables": table_details,
            "paragraph_issues": issues, "excess_exact_paragraph_blocks": duplicate_exact,
            "unsupported_in_source": gold["unsupported"],
            "limitations": ["OOXML structure/text fidelity only; no rendered layout or image/OCR scoring",
                            "Paragraph presence allows adjacent output blocks; single-block completeness is reported separately",
                            "Tables are paired in source order; one missing table can cause subsequent pair mismatches",
                            "Excess exact paragraph strings are diagnostic candidates only: hyperlinks/field references split from other paragraphs can legitimately repeat a heading string",
                            "Formatting-only headings have no independent explicit hierarchy gold",
                            "Table body/span scores project only optional numbering prefixes derived from original XML; numbering retention and raw-exact scores are separate",
                            "Numbering source retention includes raw Docling item markers; normalized-block retention is separately reported"]}


def resolve(path, directory):
    path = Path(path)
    for candidate in (path, WORKSPACE / path, directory / path):
        if candidate.exists():
            return candidate.resolve()
    return (directory / path).resolve()


def resume_scores(folder, run):
    details = []
    checks = run.get("resumeChecks", run.get("resume_checks", [])) + run.get("boundaryResumeChecks", [])
    for check in checks:
        mode = check.get("mode", "stream")
        full_path = folder / f"{mode}-events.jsonl"
        resumed_path = resolve(check.get("eventsPath", check.get("events_path", "")), folder)
        if not full_path.is_file() or not resumed_path.is_file():
            details.append({"mode": mode, "status": "MISSING_ARTIFACT", "resumed_path": str(resumed_path)})
            continue
        full = [e for e in read_events(full_path) if e.get("type") in {"batch", "complete"}]
        resumed = [e for e in read_events(resumed_path) if e.get("type") in {"batch", "complete"}]
        after = integer(check.get("afterBatch", check.get("after_batch", 0)))
        expected = full if check.get("includesPrefix", False) else full[after:]
        equal = expected == resumed
        first = next((i for i, (a, b) in enumerate(zip(expected, resumed)) if a != b), None)
        details.append({"mode": mode, "after_batches": after, "status": "PASS" if equal else "FAIL",
                        "comparison": "ALL_BATCH_AND_COMPLETE_FIELDS_EXACT",
                        "expected_events": len(expected), "resumed_events": len(resumed), "first_different_event": first})
    return {"checks": details, "passed": sum(c["status"] == "PASS" for c in details), "total": len(details),
            "status": "NOT_MEASURED" if not details else "PASS" if all(c["status"] == "PASS" for c in details) else "FAIL"}


def timing_for(run, mode):
    value = run.get(mode) or run.get("modes", {}).get(mode) or {}
    if not value and isinstance(run.get("runs"), list):
        value = next((r for r in run["runs"] if r.get("mode") == mode), {})
    return {"first_batch_ms": value.get("first_batch_ms", value.get("firstBatchMs", value.get("first_parse_ms"))),
            "total_ms": value.get("total_ms", value.get("totalMs", value.get("elapsed_ms"))),
            "peak_rss_bytes": value.get("peak_rss_bytes"),
            "basis": value.get("timing_basis", run.get("timing_basis", "See run.json; timing is not inferred from event count"))}


def report(rows, output):
    lines = ["# DOCX 增量解析与 Docling 对照", "",
             "本报告以原始 DOCX 的 OOXML 为独立参照，不把旧解析输出作为金标；不调用聊天 LLM。",
             "正文比较仅忽略空白，保留大小写、标点和数字。结果评价文字与结构保留，不等于排版准确率或问答准确率。", ""]
    for kind in ("REAL", "SYNTHETIC"):
        group = [row for row in rows if row["kind"] == kind]
        if not group:
            continue
        lines += [f"## {'真实文档' if kind == 'REAL' else '合成压力与边界用例'}", "",
                  "| 文档 | 模式 | 状态 | 完整段落 | 单块完整 | 跨 Run | 显式标题层级 | 表格单元格及跨度 | 首批 ms | 总 ms |",
                  "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
        for row in group:
            for mode, result in row["modes"].items():
                if result.get("status") == "NOT_RUN":
                    lines.append(f"| {row['key']} | {mode} | 未执行 | — | — | — | — | — | — | — |")
                    continue
                q, t = result["quality"], result["timing"]
                fraction = lambda key: f"{q[key]['matched']}/{q[key]['total']}" if q[key]["total"] else "不适用"
                lines.append(f"| {row['key']} | {mode} | {result.get('status', 'UNKNOWN')} | {fraction('paragraph_text_retained')} | {fraction('paragraph_in_single_block')} | {fraction('multi_run_paragraph_complete')} | {fraction('explicit_heading_level')} | {fraction('table_cells_text_and_spans')} | {t['first_batch_ms']} | {t['total_ms']} |")
        lines += [""]
    lines += ["## 解释与边界", "",
              "- 表格与显式标题不存在时标为不适用，不计作 100% 通过。",
              "- 真实文档与合成用例分别统计；大文件的压缩字节数、正文 XML 大小、媒体大小和文字量分别保留。",
              "- 自动编号只评分独立可解释的格式；字体符号、复杂重启规则等单独列为不支持，不冒充通过。",
              "- 图片、公式、页眉页脚等不在本轮主正文金标内；源文档存在这些内容会记录为有损范围。",
              "- 断点验证仅比较真实保存的恢复事件，逐字段匹配批次与完成事件；缺少恢复产物则为未测。", ""]
    lines += ["stream_text_only 是另行启用的非严格诊断，允许保留正文和表格并报告被忽略的内容。PARTIAL_UNSUPPORTED 不能当作完整解析通过，也不可正式入库；它不会替代 strict stream。", ""]
    lines += ["评分修订：表格正文和跨度允许原始 XML 规定的自动编号以前缀出现，并单独统计编号保留；raw-exact 指标保留原始严格字符串比较。编号结构保留检查原始 Docling item.marker，归一化块未复制 marker 的问题另列，不误判为解析结果完全丢失。", ""]
    for row in rows:
        lines += [f"### {row['key']}", "",
                  f"独立源参照：{row['gold_path']}。断点验证：{row['resume']['status']}，{row['resume']['passed']}/{row['resume']['total']}。", ""]
        features = row["features"]
        lines.append(f"正文非空段落 {features['nonempty_paragraphs']}，显式标题 {features['explicit_headings']}，表格 {features['tables']}，合并单元格 {features['merged_cells']}。")
        lines.append("")
        for mode, result in row["modes"].items():
            if result.get("status") == "NOT_RUN":
                continue
            q = result["quality"]
            lines.append(f"{mode}：段落边界问题 {len(q['paragraph_issues'])}，重复字符串候选 {len(q['excess_exact_paragraph_blocks'])}（可能来自合法超链接／字段拆分，不能直接当重复原文）；错误事件 {len(q['errors'])}。详细丢失来源、表格坐标差异及不支持项见 results.json。")
            lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=WORKSPACE / "evaluation/docx-stream-ab-20261027")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest_path = args.manifest or root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    rows = []
    for item in manifest["documents"]:
        key = item.get("key", item.get("id"))
        source = resolve(item.get("path", item.get("file")), manifest_path.parent)
        gold = ooxml_gold(source)
        expected_sha = item.get("sha256", item.get("source_sha256"))
        if expected_sha and expected_sha != gold["source"]["sha256"]:
            raise ValueError(f"Source changed for {key}: SHA-256 mismatch")
        folder = root / key
        gold_path = folder / "ooxml-gold.json"
        dump(gold_path, gold)
        kind = "SYNTHETIC" if item.get("synthetic") or "SYNTHETIC" in item.get("kind", "").upper() else "REAL"
        run_path = folder / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8-sig")) if run_path.exists() else {}
        row = {"key": key, "kind": kind, "language": item.get("language"), "source": gold["source"],
               "features": gold["features"], "gold_path": str(gold_path.relative_to(root)), "modes": {}}
        modes = ["docling", "stream"]
        if (folder / "boundary-events.jsonl").exists():
            modes.append("boundary")
        modes.extend(mode for mode in ("stream_diagnostic", "boundary_diagnostic")
                     if (folder / f"{mode}-events.jsonl").exists())
        if (folder / "stream-text-only-events.jsonl").exists() or "stream_text_only" in run:
            modes.append("stream_text_only")
        for mode in modes:
            file_mode = "stream-text-only" if mode == "stream_text_only" else mode
            path = folder / f"{file_mode}-events.jsonl"
            if args.prepare_only or not path.exists():
                row["modes"][mode] = {"status": "NOT_RUN"}
                continue
            quality = score_events(gold, read_events(path))
            status = run.get(mode, {}).get("status") or ("PARTIAL_UNSUPPORTED" if quality["partial"] else "COMPLETE" if quality["completed"] and not quality["errors"] else "ERROR")
            row["modes"][mode] = {"status": status, "diagnostic_only": mode == "stream_text_only" or mode.endswith("_diagnostic"),
                                  "quality": quality, "timing": timing_for(run, mode)}
        row["resume"] = resume_scores(folder, run) if not args.prepare_only else {"checks": [], "passed": 0, "total": 0, "status": "NOT_MEASURED"}
        rows.append(row)
    result = {"llmCalls": 0, "scoring_revision": SCORING_REVISION,
              "gold_basis": "ORIGINAL_OOXML_NOT_PARSER_OUTPUT", "documents": rows}
    if not args.prepare_only:
        previous_path = root / "results.json"
        archived_path = root / "results-before-numbering-projection.json"
        if previous_path.exists() and not archived_path.exists():
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
            if previous.get("scoring_revision") != SCORING_REVISION:
                archived_path.write_bytes(previous_path.read_bytes())
                if (root / "report.md").exists():
                    (root / "report-before-numbering-projection.md").write_bytes((root / "report.md").read_bytes())
    dump(root / ("gold-inventory.json" if args.prepare_only else "results.json"), result)
    if not args.prepare_only:
        report(rows, root / "report.md")
    print(json.dumps({"documents": len(rows), "llmCalls": 0, "prepare_only": args.prepare_only,
                      "output": str(root)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
