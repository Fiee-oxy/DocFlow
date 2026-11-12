"""Create a deterministic, clearly synthetic OOXML parser stress fixture.

This is a structural test package, not a rendered Word deliverable.  It contains
no images/fields/headers/SDTs and never modifies a real source document.
"""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

WORKSPACE = Path(__file__).resolve().parents[1]
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ET.register_namespace("w", W)


def w(name):
    return "{" + W + "}" + name


def paragraph(parent, text, style=None, numbered=False, split=None):
    node = ET.SubElement(parent, w("p"))
    if style or numbered:
        props = ET.SubElement(node, w("pPr"))
        if style:
            ET.SubElement(props, w("pStyle"), {w("val"): style})
        if numbered:
            num = ET.SubElement(props, w("numPr"))
            ET.SubElement(num, w("ilvl"), {w("val"): "0"})
            ET.SubElement(num, w("numId"), {w("val"): "1"})
    pieces = [text] if split is None else [text[:split], text[split:]]
    for piece in pieces:
        run = ET.SubElement(node, w("r"))
        value = ET.SubElement(run, w("t"), {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
        value.text = piece
    return node


def cell(row, text, span=1, vertical=None):
    value = ET.SubElement(row, w("tc"))
    if span > 1 or vertical:
        props = ET.SubElement(value, w("tcPr"))
        if span > 1:
            ET.SubElement(props, w("gridSpan"), {w("val"): str(span)})
        if vertical:
            ET.SubElement(props, w("vMerge"), {w("val"): vertical})
    paragraph(value, text, split=len(text) // 2 if text else None)


def table(parent, rows, cols):
    node = ET.SubElement(parent, w("tbl"))
    grid = ET.SubElement(node, w("tblGrid"))
    for _ in range(cols):
        ET.SubElement(grid, w("gridCol"), {w("w"): "1500"})
    for index, values in enumerate(rows):
        row = ET.SubElement(node, w("tr"))
        if index == 0:
            props = ET.SubElement(row, w("trPr"))
            ET.SubElement(props, w("tblHeader"))
        for value in values:
            cell(row, value)
    return node


def build(path, paragraphs=1200):
    if paragraphs < 1000:
        raise ValueError("Stress fixture requires at least 1,000 body paragraphs")
    document = ET.Element(w("document"))
    body = ET.SubElement(document, w("body"))
    for index in range(paragraphs):
        if index == 0:
            text, style, numbered = "SYNTHETIC OOXML streaming quality stress fixture", "Heading1", False
        elif index % 100 == 0:
            text, style, numbered = f"Chapter {index // 100 + 1} synthetic section", "Heading1", False
        elif index % 25 == 0:
            text, style, numbered = f"Subsection {index:04d} source preservation", "Heading2", False
        else:
            digest = hashlib.sha256(f"docx-stress-v1:{index}".encode()).hexdigest()[:24]
            text = (f"Paragraph {index:04d}: Reference CODE{index:06d} requires exactly {index + 17} credits. "
                    f"Preserve the complete multi-run sentence and source order. Evidence {digest}.")
            style, numbered = None, index % 10 == 0
        # Split within a numeric reference, not merely at a word boundary.
        split = text.find("CODE") + 6 if "CODE" in text else max(1, len(text) // 2)
        paragraph(body, text, style, numbered, split)
        if index == 300:
            merged = ET.SubElement(body, w("tbl"))
            grid = ET.SubElement(merged, w("tblGrid"))
            for _ in range(3):
                ET.SubElement(grid, w("gridCol"), {w("w"): "1600"})
            row = ET.SubElement(merged, w("tr"))
            props = ET.SubElement(row, w("trPr")); ET.SubElement(props, w("tblHeader"))
            cell(row, "SYNTHETIC merged header", span=2); cell(row, "Code")
            row = ET.SubElement(merged, w("tr"))
            cell(row, "Vertical group A", vertical="restart"); cell(row, "Required"); cell(row, "160")
            row = ET.SubElement(merged, w("tr"))
            cell(row, "", vertical="continue"); cell(row, "Optional"); cell(row, "12")
        if index == 700:
            rows = [["Row key", "Term", "Credits", "Category", "Checksum", "Notes"]]
            for row in range(1, 101):
                rows.append([f"TROW{row:04d}", f"Term {row % 8 + 1}", str(row + 10),
                             f"Category {row % 3}", hashlib.sha256(f"row{row}".encode()).hexdigest()[:16],
                             f"Complete atomic table row {row}"])
            table(body, rows, 6)
    sect = ET.SubElement(body, w("sectPr"))
    ET.SubElement(sect, w("pgSz"), {w("w"): "11906", w("h"): "16838"})

    styles = ET.Element(w("styles"))
    for name, level in [("Normal", None), ("Heading1", 0), ("Heading2", 1)]:
        style = ET.SubElement(styles, w("style"), {w("type"): "paragraph", w("styleId"): name})
        ET.SubElement(style, w("name"), {w("val"): name})
        if level is not None:
            props = ET.SubElement(style, w("pPr"))
            ET.SubElement(props, w("outlineLvl"), {w("val"): str(level)})
    numbering = ET.Element(w("numbering"))
    abstract = ET.SubElement(numbering, w("abstractNum"), {w("abstractNumId"): "1"})
    level = ET.SubElement(abstract, w("lvl"), {w("ilvl"): "0"})
    ET.SubElement(level, w("start"), {w("val"): "1"})
    ET.SubElement(level, w("numFmt"), {w("val"): "decimal"})
    ET.SubElement(level, w("lvlText"), {w("val"): "%1."})
    number = ET.SubElement(numbering, w("num"), {w("numId"): "1"})
    ET.SubElement(number, w("abstractNumId"), {w("val"): "1"})
    parts = {
        "[Content_Types].xml": '''<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/></Types>'''.encode(),
        "_rels/.rels": f'''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>'''.encode(),
        "word/_rels/document.xml.rels": f'''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rStyles" Type="{R}/styles" Target="styles.xml"/><Relationship Id="rNum" Type="{R}/numbering" Target="numbering.xml"/></Relationships>'''.encode(),
        "word/document.xml": ET.tostring(document, encoding="utf-8", xml_declaration=True),
        "word/styles.xml": ET.tostring(styles, encoding="utf-8", xml_declaration=True),
        "word/numbering.xml": ET.tostring(numbering, encoding="utf-8", xml_declaration=True)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(parts.items()):
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, content)
    return {"key": f"synthetic_stress_{paragraphs}", "path": path.relative_to(WORKSPACE).as_posix(),
            "kind": "SYNTHETIC", "synthetic": True, "language": "en",
            "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "document_xml_bytes": len(parts["word/document.xml"]),
            "fixture_version": "docx-stress-v1", "body_paragraphs": paragraphs,
            "note": "Synthetic structural stress only: multi-run text, explicit heading hierarchy, numbering, one merged short table and one complete 101x6 table; no production source modified"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=WORKSPACE / "evaluation/docx-stream-ab-20261027")
    parser.add_argument("--paragraphs", type=int, default=1200)
    args = parser.parse_args()
    root = args.root.resolve()
    item = build(root / f"inputs/synthetic_stress_{args.paragraphs}.docx", args.paragraphs)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["documents"] = [d for d in manifest["documents"] if d.get("key") != item["key"]] + [item]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(item, ensure_ascii=True))


if __name__ == "__main__":
    main()
