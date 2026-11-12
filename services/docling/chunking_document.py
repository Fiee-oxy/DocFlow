"""Build a Docling view of repaired source units without altering raw conversion data.

The original converter JSON remains in ``doclingDocument`` for diagnostics.  This
view is used only when a batch's source units have changed at page boundaries.
Imports are deliberately lazy: native FAST extraction does not load docling-core.
"""
from copy import deepcopy


def _original_items(raw):
    return {item.get("self_ref"): item
            for category in ("texts", "tables", "pictures")
            for item in raw.get(category, [])}


def _fragments(block, originals):
    if block.get("source_fragments"):
        return block["source_fragments"]
    ref = block.get("ref", "")
    original = originals.get(ref, {})
    fragment = {"ref": ref, "pages": deepcopy(block.get("pages", [])),
                "text": block.get("text", "")}
    for key in ("batch_index", "bbox", "page_size"):
        if key in block:
            fragment[key] = deepcopy(block[key])
    prov = block.get("prov") or original.get("prov")
    if prov:
        fragment["prov"] = deepcopy(prov)
    block["source_fragments"] = [fragment]
    return block["source_fragments"]


def attach_chunking_document(event):
    """Attach a validated structural view and map canonical refs to raw sources.

    Caller chooses whether a transformed Docling batch needs this view.  The raw
    ``doclingDocument`` and all original source fragments are left untouched.
    Table cells, headers and row/column spans come from ``table_data``; markdown
    is never reverse-engineered into a table.  Without structured data the
    existing markdown is safely retained as text.
    """
    raw = event.get("doclingDocument")
    if not raw:
        return event
    from docling_core.types.doc import DoclingDocument, DocItemLabel, GroupLabel, ProvenanceItem, Size, TableData

    originals = _original_items(raw)
    document = DoclingDocument(name=raw.get("name") or "boundary-repaired-document")
    pages = {}
    for number, page in raw.get("pages", {}).items():
        if page.get("size"):
            pages[int(number)] = page["size"]

    list_group = None
    list_kind = None
    for block in event.get("blocks", []):
        fragments = _fragments(block, originals)
        label = block.get("label", "text")
        text = block.get("text", "")
        original = originals.get(block.get("ref"), {})
        table_data = block.get("table_data")
        if label != "list_item":
            list_group = None
            list_kind = None
        if label == "table" and table_data:
            item = document.add_table(data=TableData.model_validate(deepcopy(table_data)))
        elif label == "section_header":
            item = document.add_heading(text=text, level=max(1, min(100, int(block.get("heading_level") or 1))))
        elif label == "title":
            item = document.add_title(text=text)
        elif label == "list_item":
            enumerated = block.get("enumerated", original.get("enumerated", False))
            kind = GroupLabel.ORDERED_LIST if enumerated else GroupLabel.LIST
            if list_group is None or kind != list_kind:
                list_group = document.add_group(label=kind)
                list_kind = kind
            item = document.add_list_item(text=text, enumerated=enumerated,
                                          marker=block.get("marker", original.get("marker", "")), parent=list_group)
        else:
            # A missing table model must not be invented from rendered markdown.
            try:
                text_label = DocItemLabel(label) if label != "table" else DocItemLabel.TEXT
            except ValueError:
                text_label = DocItemLabel.TEXT
            item = document.add_text(label=text_label, text=text)

        provenance = []
        seen = set()
        for fragment in fragments:
            for p in fragment.get("prov", []):
                # Raw fragment provenance keeps original character spans.  The
                # canonical item's spans address its own newly joined text.
                value = deepcopy(p)
                if label != "table":
                    fragment_text = fragment.get("text", "")
                    offset = text.find(fragment_text) if fragment_text else -1
                    if offset >= 0:
                        value["charspan"] = [offset, offset + len(fragment_text)]
                    else:
                        value["charspan"] = [0, len(text)]
                parsed = ProvenanceItem.model_validate(value)
                signature = parsed.model_dump_json()
                if signature not in seen:
                    provenance.append(parsed)
                    seen.add(signature)
            size = fragment.get("page_size") or block.get("page_size")
            if size:
                if "width" in size and "height" in size:
                    for number in fragment.get("pages", []):
                        pages.setdefault(int(number), size)
                else:
                    for number, dimensions in size.items():
                        if isinstance(dimensions, dict) and "width" in dimensions:
                            pages.setdefault(int(number), dimensions)
        if not provenance:
            for p in block.get("prov") or original.get("prov", []):
                provenance.append(ProvenanceItem.model_validate(deepcopy(p)))
        item.prov = provenance
        block["ref"] = item.self_ref

    for number, size in sorted(pages.items()):
        document.add_page(page_no=number, size=Size.model_validate(size))
    event["chunkingDocument"] = document.export_to_dict()
    return event
