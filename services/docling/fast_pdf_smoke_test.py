"""Small native-text PDF integration check; no LLM or neural parsing models."""
import json
import time
import requests
from smoke_test import pdf_bytes


if __name__ == "__main__":
    for resume in (0, 1):
        started = time.monotonic()
        response = requests.post("http://127.0.0.1:5001/parse-stream",
            files={"file": ("smoke.pdf", pdf_bytes())},
            data={"parse_mode": "FAST", "resume_pages": resume, "resume_batches": resume},
            stream=True, timeout=30)
        response.raise_for_status()
        events = [json.loads(line) for line in response.iter_lines()]
        assert events[-1]["type"] == "complete", events
        blocks = [block for event in events if event["type"] == "batch" for block in event["blocks"]]
        assert len(blocks) == 2 - resume
        assert blocks[0]["pages"] == [resume + 1]
        assert any("Tail-only fact" in block["text"] and block["pages"] == [2] for block in blocks)
        print({"mode": "FAST", "resumePages": resume, "seconds": round(time.monotonic() - started, 3),
               "physicalPagesCorrect": True}, flush=True)
