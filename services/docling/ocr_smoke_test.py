"""Image-only bilingual PDF through both production parsing routes."""
import io
import json
import time
import requests
from PIL import Image, ImageDraw, ImageFont

image = Image.new("RGB", (1600, 900), "white")
draw = ImageDraw.Draw(image)
font = ImageFont.truetype("/tmp/msyh.ttc", 48)
draw.text((90, 120), "OCR TEST: Graduation requires 160 credits.", font=font, fill="black")
draw.text((90, 260), "毕业要求修满160学分。", font=font, fill="black")
pdf = io.BytesIO()
image.save(pdf, format="PDF", resolution=144)
for mode in ["FAST", "DOCLING"]:
    started = time.monotonic()
    response = requests.post("http://127.0.0.1:5001/parse-stream",
        files={"file": ("scan.pdf", pdf.getvalue(), "application/pdf")},
        data={"parse_mode": mode}, timeout=240)
    response.raise_for_status()
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1]["type"] == "complete", events[-1]
    blocks = [b for e in events if e["type"] == "batch" for b in e["blocks"]]
    text = "\n".join(b["text"] for b in blocks)
    assert "160" in text and "credits" in text and "学分" in "".join(text.split()), text
    assert all(b["pages"] == [1] for b in blocks), blocks
    print(json.dumps({"mode": mode, "seconds": round(time.monotonic()-started, 2),
        "blocks": len(blocks), "text": text}, ensure_ascii=False), flush=True)
