"""Synthetic integration checks; run inside a test parser with batch size 1."""
import json
import time
import requests
from smoke_test import pdf_bytes, docx_bytes


def run(suffix, content, resume=None):
    started = time.monotonic()
    events = []
    with requests.post("http://127.0.0.1:5001/parse-stream",
                       files={"file": ("smoke." + suffix, content)}, data=resume or {},
                       stream=True, timeout=(10, 240)) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            event = json.loads(line)
            if event["type"] == "heartbeat":
                continue
            assert event["type"] != "error", event
            events.append(event)
            print({"format": suffix, "event": event["type"], "endPage": event.get("endPage"),
                   "seconds": round(time.monotonic() - started, 2)}, flush=True)
    assert events[-1]["type"] == "complete", events
    return [e for e in events if e["type"] == "batch"]


if __name__ == "__main__":
    batches = run("pdf", pdf_bytes())
    assert [e["endPage"] for e in batches] == [1, 2]
    assert all(p == b["endPage"] for b in batches for item in b["blocks"] for p in item["pages"])
    resumed = run("pdf", pdf_bytes(), {"resume_pages": 1, "resume_batches": 1, "resume_heading": "Chapter"})
    assert len(resumed) == 1 and resumed[0]["startPage"] == 2
    assert any("Tail-only fact" in b["text"] for b in resumed[0]["blocks"])
    docx = run("docx", docx_bytes())
    assert any("Redis" in b["text"] for b in docx[0]["blocks"])
    print("Pipeline batches, physical page numbers, resume and DOCX: PASS", flush=True)
