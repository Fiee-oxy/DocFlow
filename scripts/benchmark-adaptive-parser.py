"""Run inside the parser image. No LLM/index or production writes.

python benchmark-adaptive-parser.py input.pdf output.json
Compares explicit native/Docling and page AUTO on the exact same source.
"""
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")
from pipeline import task_events


def main():
    source, destination = map(Path, sys.argv[1:3])
    results = []
    for mode in ("FAST", "DOCLING", "AUTO"):
        settings = dict(parse_mode=mode, max_pages=1000, max_bytes=209715200,
                        max_chars=10000000, resume_pages=0, resume_batches=0,
                        resume_state=None, heading="", batch_pages=1, fast_batch_pages=1,
                        ocr_enabled=True, ocr_languages=["chi_sim", "eng"],
                        threads=2, timeout=180, table_mode="fast", continuity_enabled=True)
        began=time.monotonic(); first=None; events=[]
        for event in task_events(str(source),settings):
            if event["type"] == "batch" and first is None:
                first=time.monotonic()-began
            if event["type"] != "heartbeat":events.append(event)
        batches=[e for e in events if e["type"] == "batch"]
        output=destination.with_name(destination.stem+"-"+mode+"-events.json")
        output.write_text(json.dumps(events,ensure_ascii=False),encoding="utf-8")
        results.append(dict(mode=mode,elapsedSeconds=time.monotonic()-began,
                            firstBatchSeconds=first,completed=events[-1]["type"]=="complete",
                            batches=len(batches),characters=sum(len(b.get("text","")) for e in batches for b in e["blocks"]),
                            parserRoutes=[e.get("parser") for e in batches],
                            errors=[e for e in events if e["type"]=="error"],eventsFile=output.name))
        destination.write_text(json.dumps(dict(source=str(source),sourceSha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            chatLlmCalls=0,indexWrites=0,measurement="PARSER_ONLY_NOT_RETRIEVAL_OR_ANSWER_ACCURACY",results=results),ensure_ascii=False,indent=2),encoding="utf-8")


if __name__ == "__main__":main()
