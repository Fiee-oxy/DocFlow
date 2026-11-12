"""Run inside the parser container: python smoke_test.py [--pdf]. Uses synthetic data."""
import argparse
import io
import requests
from docx import Document


def docx_bytes():
    document = Document()
    document.add_heading("Chapter One", 1)
    document.add_paragraph("A distributed lock coordinates concurrent access across service instances.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Component"
    table.cell(0, 1).text = "Role"
    table.cell(1, 0).text = "Redis"
    table.cell(1, 1).text = "Cache"
    document.add_heading("Last Chapter", 1)
    document.add_paragraph("Tail-only fact: retries must preserve successful checkpoints.")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def pdf_bytes():
    # Two valid text PDF pages; no external font or document download.
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for text in (b"Distributed locks coordinate concurrent access.", b"Tail-only fact: preserve successful checkpoints."):
        stream = b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET"
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", action="store_true")
    options = parser.parse_args()
    suffix = "pdf" if options.pdf else "docx"
    content = pdf_bytes() if options.pdf else docx_bytes()
    response = requests.post("http://127.0.0.1:5001/parse", files={"file": ("smoke." + suffix, content)}, timeout=1200)
    response.raise_for_status()
    result = response.json()
    assert any("Tail-only fact" in block["text"] for block in result["blocks"]), result
    assert all(block["ref"] for block in result["blocks"])
    if options.pdf:
        assert any(2 in block["pages"] for block in result["blocks"])
    else:
        assert any("Redis" in block["text"] and "Cache" in block["text"] for block in result["blocks"])
    print({"format": suffix, "blocks": len(result["blocks"]), "tail_preserved": True, "warnings": result["warnings"]})
