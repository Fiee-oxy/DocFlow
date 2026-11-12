"""Conservative, bounded PDF boundary stitching without model inference.

Only normalized blocks with physical-page and geometry evidence are joined.
Original source fragments are always retained, even when a display hyphen or a
repeated table header is removed. A caller can persist the final block as carry
state and feed it to ``merge_boundary`` with the next batch's first block.
"""
from copy import deepcopy
import hashlib
import json
import math
import re
import unicodedata


MAX_CHARS = 20000
MAX_TABLE_CELLS = 2000
MAX_FRAGMENTS = 16
_PROSE = {"text", "paragraph"}
_UNKNOWN_SECTIONS = {"", "正文", "body", "text"}
_NEW_ITEM = re.compile(
    r"^(?:#{1,6}\s|[-*•●]\s+|(?:\d+|[一二三四五六七八九十百]+)[、．.)）]\s*"
    r"|[（(](?:\d+|[一二三四五六七八九十百]+)[）)]\s*"
    r"|第[一二三四五六七八九十百千万\d]+[章节篇部]|chapter\s+\d+\b)", re.I)
_TERMINAL = re.compile(r"[.!?。！？;；:：][\s\"'”’）)\]】》]*$")


def source_fragments(block):
    """Return detached, original source records; never nest prior merges."""
    fragments = block.get("source_fragments")
    if isinstance(fragments, list) and fragments:
        return deepcopy(fragments)
    fragment = deepcopy({key: value for key, value in block.items()
                         if key not in {"continuity", "source_fragments"}})
    # Docling refs repeat between batches, so the physical page is essential.
    origin = json.dumps([fragment.get("pages", []), fragment.get("ref", ""),
                         fragment.get("batch_index")],
                        ensure_ascii=False, separators=(",", ":"))
    fragment["origin_id"] = hashlib.sha256(origin.encode("utf-8")).hexdigest()
    return [fragment]


def _pages(block):
    pages = block.get("pages")
    if not isinstance(pages, list) or not pages:
        return None
    if any(isinstance(page, bool) or not isinstance(page, int) or page < 1 for page in pages):
        return None
    return sorted(set(pages))


def _same_section(left, right):
    a = re.sub(r"\s+", " ", str(left.get("section", ""))).strip()
    b = re.sub(r"\s+", " ", str(right.get("section", ""))).strip()
    # Unknown sections are not treated as a known semantic heading.
    return a == b or (a.casefold() in _UNKNOWN_SECTIONS and b.casefold() in _UNKNOWN_SECTIONS)


def _edge_position(block, last):
    fragments = block.get("source_fragments")
    source = fragments[-1 if last else 0] if fragments else block
    box, size = source.get("bbox"), source.get("page_size")
    if not isinstance(box, dict) or not isinstance(size, dict):
        return None
    try:
        height = float(size["height"])
        top, bottom = float(box["t"]), float(box["b"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (height, top, bottom)) or height <= 0:
        return None
    origin = str(box.get("coord_origin", "")).upper()
    if origin == "BOTTOMLEFT":
        if top < bottom:
            return None
        top, bottom = height - top, height - bottom
    elif origin == "TOPLEFT":
        if top > bottom:
            return None
    else:
        return None
    if top < 0 or bottom > height or top > bottom:
        return None
    return top / height, bottom / height


def _at_edges(left, right):
    a, b = _edge_position(left, True), _edge_position(right, False)
    return a is not None and b is not None and a[1] >= .65 and b[0] <= .35


def _joined(left, right, text, kind, reason, **details):
    merged = deepcopy(left)
    fragments = source_fragments(left) + source_fragments(right)
    merged.update(text=text, pages=sorted(set(left["pages"] + right["pages"])),
                  source_fragments=fragments,
                  continuity={"version": 1, "kind": kind, "reason": reason,
                              "source_count": len(fragments), **details})
    # A single merged bbox would falsely imply that multiple pages share one box.
    merged.pop("bbox", None)
    merged.pop("page_size", None)
    return merged


def _merge_prose(left, right):
    if left.get("label", "text") not in _PROSE or right.get("label", "text") not in _PROSE:
        return None
    if not _at_edges(left, right):
        return None
    a, b = left["text"].rstrip(), right["text"].lstrip()
    if not a or not b or _NEW_ITEM.match(b) or _TERMINAL.search(a):
        return None
    # A capitalized new sentence, list or number is not inferred to continue.
    first = b[0]
    if not (first.islower() or "\u3400" <= first <= "\u9fff"):
        return None
    hyphenated = bool(re.search(r"[A-Za-z]-$", a) and first.isascii() and first.islower())
    if hyphenated:
        text = a[:-1] + b
    elif "\u3400" <= a[-1] <= "\u9fff" and "\u3400" <= first <= "\u9fff":
        text = a + b
    else:
        text = a + " " + b
    return _joined(left, right, text, "paragraph", "adjacent_page_edges_unfinished_sentence",
                   dehyphenated=hyphenated)


def _table_cells(data):
    if not isinstance(data, dict):
        return None
    rows, cols = data.get("num_rows"), data.get("num_cols")
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in (rows, cols)):
        return None
    cells = data.get("table_cells")
    if cells is None and isinstance(data.get("grid"), list):
        cells, seen = [], set()
        for row in data["grid"]:
            if not isinstance(row, list):
                return None
            for cell in row:
                if not isinstance(cell, dict):
                    return None
                marker = json.dumps(cell, sort_keys=True, ensure_ascii=False)
                if marker not in seen:
                    cells.append(cell)
                    seen.add(marker)
    if not isinstance(cells, list) or not cells:
        return None
    offsets = ("start_row_offset_idx", "end_row_offset_idx",
               "start_col_offset_idx", "end_col_offset_idx")
    occupied = set()
    for cell in cells:
        if not isinstance(cell, dict) or any(isinstance(cell.get(k), bool) or
                                            not isinstance(cell.get(k), int) for k in offsets):
            return None
        r0, r1, c0, c1 = (cell[k] for k in offsets)
        if not (0 <= r0 < r1 <= rows and 0 <= c0 < c1 <= cols):
            return None
        # The bound also prevents unexpectedly large merged spans consuming RAM.
        if (r1 - r0) * (c1 - c0) > MAX_TABLE_CELLS:
            return None
        slots = {(r, c) for r in range(r0, r1) for c in range(c0, c1)}
        if occupied.intersection(slots):
            return None
        occupied.update(slots)
        if len(occupied) > MAX_TABLE_CELLS:
            return None
    return rows, cols, cells


def _normal(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text))).casefold()


def _header(rows, cols, cells):
    header = [cell for cell in cells if cell.get("column_header") is True]
    if not header or not any(_normal(cell.get("text", "")) for cell in header):
        return None
    count = max(cell["end_row_offset_idx"] for cell in header)
    if count >= rows:
        return None  # Header-only tables provide no body continuation evidence.
    occupied = {(r, c) for cell in header
                for r in range(cell["start_row_offset_idx"], cell["end_row_offset_idx"])
                for c in range(cell["start_col_offset_idx"], cell["end_col_offset_idx"])}
    if occupied != {(r, c) for r in range(count) for c in range(cols)}:
        return None
    if any(cell["start_row_offset_idx"] < count and not cell.get("column_header") for cell in cells):
        return None
    signature = sorted((cell["start_row_offset_idx"], cell["end_row_offset_idx"],
                        cell["start_col_offset_idx"], cell["end_col_offset_idx"],
                        _normal(cell.get("text", ""))) for cell in header)
    return count, signature


_CONTINUED = r"(?:continued|cont\.|续表|续)"


def _caption_base(value):
    return re.sub(r"[\W_]+", "", re.sub(_CONTINUED, "", value, flags=re.I)).casefold()


def _continued_caption(left, right):
    a, b = str(left.get("caption", "")), str(right.get("caption", ""))
    return bool(a and re.search(_CONTINUED, b, re.I) and _caption_base(a) and
                _caption_base(a) == _caption_base(b))


def _without_repeated_markdown_header(text, cells, header_rows, cols):
    """Remove only header lines whose cells exactly match the structured header."""
    lines = text.splitlines(keepends=True)
    expected = [[""] * cols for _ in range(header_rows)]
    for cell in cells:
        if cell.get("column_header"):
            # Do not guess how a multi-row/column span was rendered to Markdown.
            if cell["end_row_offset_idx"] - cell["start_row_offset_idx"] != 1 or \
                    cell["end_col_offset_idx"] - cell["start_col_offset_idx"] != 1:
                return text, False
            expected[cell["start_row_offset_idx"]][cell["start_col_offset_idx"]] = cell.get("text", "")
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    for row in expected:
        if index >= len(lines):
            return text, False
        line = lines[index].strip()
        if not line.startswith("|") or not line.endswith("|"):
            return text, False
        actual = line[1:-1].split("|")
        if len(actual) != cols or [_normal(value) for value in actual] != [_normal(value) for value in row]:
            return text, False
        index += 1
        # Docling may insert a Markdown separator after its first header row.
        if index < len(lines) and re.fullmatch(r"\s*\|?[\s|:\-]+\|?\s*", lines[index]):
            index += 1
    body = "".join(lines[index:])
    return (body, True) if body.strip() else (text, False)


def _merge_table(left, right, max_table_cells):
    ac, bc = str(left.get("caption", "")).strip(), str(right.get("caption", "")).strip()
    if bc and not ac:
        return None  # A newly captioned table may start independently on the next page.
    if ac and bc and _caption_base(ac) != _caption_base(bc):
        return None  # Explicitly different captions override coincidentally equal headers.
    a, b = _table_cells(left.get("table_data")), _table_cells(right.get("table_data"))
    if a is None or b is None or a[1] != b[1] or len(a[2]) + len(b[2]) > max_table_cells:
        return None
    ah, bh = _header(*a), _header(*b)
    if ah is None or bh is None or ah != bh:
        return None
    if not (_at_edges(left, right) or _continued_caption(left, right)):
        return None
    repeated = bh[0]
    merged_rows = a[0] + b[0] - repeated
    # A handful of spanning cells may expand into a very large Docling grid.
    # Bound that allocation as well as the number of serialized cell objects.
    if merged_rows * a[1] > max_table_cells:
        return None
    cells = deepcopy(a[2])
    for cell in deepcopy(b[2]):
        if cell["end_row_offset_idx"] <= repeated:
            continue
        # A body cell crossing the header boundary is ambiguous, never clip it.
        if cell["start_row_offset_idx"] < repeated:
            return None
        cell["start_row_offset_idx"] += a[0] - repeated
        cell["end_row_offset_idx"] += a[0] - repeated
        cells.append(cell)
    body, removed = _without_repeated_markdown_header(right["text"], b[2], repeated, b[1])
    merged = _joined(left, right, left["text"].rstrip() + "\n" + body.lstrip(), "table",
                     "matching_structured_header_and_page_continuity",
                     repeated_header_rows=repeated, display_header_removed=removed)
    # Source-local cell bboxes remain queryable in source_fragments. A combined
    # table has row offsets spanning pages, so remove its misleading cell bboxes.
    for cell in cells:
        cell.pop("bbox", None)
    merged["table_data"] = {"num_rows": merged_rows,
                            "num_cols": a[1], "table_cells": cells}
    return merged


def can_defer(block, *, max_chars=MAX_CHARS, max_table_cells=MAX_TABLE_CELLS,
              max_fragments=MAX_FRAGMENTS):
    """Whether a page tail is eligible for a bounded next-batch carry.

    Use the exact same geometry convention as merge_boundary; unknown origins
    and completed prose are not deferred merely because they occur near a page
    bottom. Actual continuation still has to pass merge_boundary next time.
    """
    if not isinstance(block, dict) or block.get("label") not in _PROSE | {"table"}:
        return False
    if not isinstance(block.get("text"), str) or not block["text"].strip() or not _pages(block):
        return False
    if block.get("layout_ambiguous", False):
        return False
    fragments = source_fragments(block)
    if len(fragments) >= max_fragments or sum(len(str(part.get("text", "")))
                                             for part in fragments) >= max_chars:
        return False
    edge = _edge_position(block, True)
    if edge is None or edge[1] < .65:
        return False
    if block.get("label") in _PROSE:
        return _TERMINAL.search(block["text"].rstrip()) is None
    data = _table_cells(block.get("table_data"))
    return (data is not None and len(data[2]) < max_table_cells
            and data[0] * data[1] < max_table_cells and _header(*data) is not None)


def merge_boundary(left, right, *, max_chars=MAX_CHARS, max_table_cells=MAX_TABLE_CELLS,
                   max_fragments=MAX_FRAGMENTS):
    """Join a confident adjacent-page continuation, or return ``None``.

    Inputs are never mutated. Unknown geometry, headings, lists, changed
    sections, completed sentences and ambiguous tables are deliberately kept
    separate. ``table_data`` accepts Docling's exported TableData dictionary.
    """
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    if left.get("layout_ambiguous", False) or right.get("layout_ambiguous", False):
        return None
    if not isinstance(left.get("text"), str) or not isinstance(right.get("text"), str):
        return None
    if not left["text"].strip() or not right["text"].strip():
        return None
    lp, rp = _pages(left), _pages(right)
    if lp is None or rp is None or lp[-1] + 1 != rp[0] or not _same_section(left, right):
        return None
    fragments = source_fragments(left) + source_fragments(right)
    if len(fragments) > max_fragments or sum(len(str(part.get("text", "")))
                                            for part in fragments) > max_chars:
        return None
    if left.get("label") == right.get("label") == "table":
        return _merge_table(left, right, max_table_cells)
    return _merge_prose(left, right)
