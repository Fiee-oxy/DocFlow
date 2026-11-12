"""Parser-image benchmark: bounded preflight vs full preflight and existing parsers."""
import json
import hashlib
import sys
import time
from pathlib import Path
sys.path.insert(0,"/app")
from adaptive_pipeline import profile_pdf,profile_docx
from prescan import prescan_document
from pipeline import task_events

SETTINGS=dict(max_pages=1000,max_bytes=209715200,max_chars=10000000,resume_pages=0,resume_batches=0,
    resume_state=None,heading="",batch_pages=1,fast_batch_pages=1,ocr_enabled=True,
    ocr_languages=["chi_sim","eng"],threads=2,timeout=180,table_mode="fast",continuity_enabled=True,
    prescan_max_pages=12,prescan_budget_ms=2000)


def main():
    inputs,out=map(Path,sys.argv[1:3]);out.mkdir(exist_ok=True)
    profiles=[];parses=[]
    def save():
        (out/"results.json").write_text(json.dumps(dict(profiles=profiles,parses=parses,chatLlmCalls=0,indexWrites=0),ensure_ascii=False,indent=2),encoding="utf-8")
    for path in sorted(inputs.iterdir()):
        if path.suffix not in (".pdf",".docx"):continue
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        for kind in ("FULL_PREFLIGHT","BOUNDED_PREFLIGHT"):
            times=[]
            for _ in range(3):
                start=time.monotonic()
                result=(profile_pdf(str(path),SETTINGS) if path.suffix==".pdf" else profile_docx(str(path),SETTINGS)) if kind=="FULL_PREFLIGHT" else prescan_document(str(path),SETTINGS)
                times.append(time.monotonic()-start)
            profiles.append(dict(document=path.name,sha256=sha,kind=kind,seconds=times,profile=result));save()
        print("profile",path.name,flush=True)
        # Full-file parse test on both languages; large files still receive full
        # preflight benchmarks, not falsely labelled full neural conversion.
        if path.name not in ("perf-5a023e1dce750ab2.pdf","perf-c6e7e73466012702.pdf"):continue
        for arm,mode,enabled in (("ORIGINAL_FAST","FAST",False),("OLD_AUTO","AUTO",False),("PRESCAN_AUTO","AUTO",True)):
            start=time.monotonic();first=None;events=[]
            for event in task_events(str(path),SETTINGS | {"parse_mode":mode,"prescan_enabled":enabled}):
                if event["type"]=="heartbeat":continue
                if event["type"]=="batch" and first is None:first=time.monotonic()-start
                events.append(event)
            elapsed=time.monotonic()-start
            name=path.stem+"-"+arm+".json"
            (out/name).write_text(json.dumps(events,ensure_ascii=False),encoding="utf-8")
            parses.append(dict(document=path.name,arm=arm,seconds=elapsed,firstBatchSeconds=first,
                complete=events[-1]["type"]=="complete",eventsFile=name,
                characters=sum(len(b["text"]) for e in events if e["type"]=="batch" for b in e["blocks"])))
            save();print("parse",path.name,arm,round(elapsed,3),events[-1]["type"],flush=True)


if __name__=="__main__":main()
