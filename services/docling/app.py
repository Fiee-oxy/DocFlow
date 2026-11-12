"""Internal Docling adapter with resumable batch streaming and task isolation."""
import os
import json
import tempfile
import threading
import logging
import time
from pathlib import Path
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from pipeline import task_events, encode_event

app = FastAPI(title="DoVideo document parser", version="2.0.0")
MAX_BYTES = int(os.getenv("DOCUMENT_MAX_FILE_BYTES", "209715200"))
MAX_PAGES = int(os.getenv("DOCUMENT_MAX_PAGES", "1000"))
MAX_TEXT_CHARS = int(os.getenv("DOCUMENT_MAX_TEXT_CHARS", "10000000"))
BATCH_PAGES = max(1, min(20, int(os.getenv("DOCUMENT_PARSE_BATCH_PAGES", "4"))))
BATCH_TIMEOUT = max(10, int(os.getenv("DOCUMENT_PARSE_BATCH_TIMEOUT_SECONDS", "180")))
PARSER_WORKERS = max(1, min(16, int(os.getenv("DOCUMENT_PARSER_WORKERS", "1"))))
lock = threading.BoundedSemaphore(PARSER_WORKERS)
FAST_WORKERS = max(1, min(16, int(os.getenv("DOCUMENT_FAST_PARSER_WORKERS", "4"))))
TABLE_MODE = os.getenv("DOCUMENT_TABLE_MODE", "accurate").lower()
if TABLE_MODE not in {"fast", "accurate"}:
    raise ValueError("DOCUMENT_TABLE_MODE must be fast or accurate")
fast_lock = threading.BoundedSemaphore(FAST_WORKERS)
DOCX_STREAM_WORKERS = max(1, min(16, int(os.getenv("DOCUMENT_DOCX_STREAM_WORKERS", "2"))))
docx_stream_lock = threading.BoundedSemaphore(DOCX_STREAM_WORKERS)
_cleanup_pending=[]
_cleanup_lock=threading.Lock()

def cleanup_pending():
    # Only TemporaryDirectory objects created by this process are eligible.
    with _cleanup_lock:
        for directory in list(_cleanup_pending):
            try:directory.cleanup();_cleanup_pending.remove(directory)
            except OSError:pass

def _cleanup_loop():
    while True:
        time.sleep(60)
        cleanup_pending()

threading.Thread(target=_cleanup_loop,name="parser-temp-janitor",daemon=True).start()
OCR_ENABLED = os.getenv("DOCUMENT_OCR_ENABLED", "true").lower() == "true"
OCR_LANGUAGES = os.getenv("DOCUMENT_OCR_LANGUAGES", "chi_sim,eng").split(",")

@app.get("/viewer", include_in_schema=False)
def viewer():
    return FileResponse(Path(__file__).with_name("viewer.html"))

@app.middleware("http")
async def limit_request(request, call_next):
    from fastapi.responses import JSONResponse
    length = request.headers.get("content-length")
    if length and int(length) > MAX_BYTES + 1024 * 1024:
        return JSONResponse(status_code=413, content={"detail": "Document exceeds file limit"})
    return await call_next(request)

@app.get("/health")
def health():
    cleanup_pending()
    return {"status": "ok", "ocr": OCR_ENABLED, "ocrLanguages": OCR_LANGUAGES, "ocrEngine": "tesseract-cli", "fastOcrFallback": OCR_ENABLED, "maxBytes": MAX_BYTES, "maxPages": MAX_PAGES,
            "continuityEnabled": os.getenv("DOCUMENT_CONTINUITY_ENABLED", "true").lower() == "true", "continuityVersion": "continuity-v1", "resumeState": True,
            "batchPages": BATCH_PAGES, "batchTimeoutSeconds": BATCH_TIMEOUT, "parserWorkers": PARSER_WORKERS,
            "fastParserWorkers": FAST_WORKERS, "fastPdf": True, "streaming": True, "tableMode": TABLE_MODE,
            "docxStreaming": True, "docxStreamWorkers": DOCX_STREAM_WORKERS,
            "docxRichContent": {"tables": True, "nestedTables": True, "imageResources": True,
                                "imageOcr": OCR_ENABLED, "headersFooters": True,
                                "fieldEvaluation": "SAVED_RESULT_ONLY", "revisionView": "FINAL_SAVED_VIEW"},
            "docxBatchNodes": max(1, int(os.getenv("DOCUMENT_DOCX_BATCH_NODES", "32"))),
            "docxBatchChars": max(1, int(os.getenv("DOCUMENT_DOCX_BATCH_CHARS", "12000"))),
            "adaptiveParserRevision": "adaptive-parser-v1",
            "prescan": {"revision":"prescan-v1", "autoEnabled":os.getenv("DOCUMENT_PRESCAN_ENABLED","false").lower()=="true",
                        "maxSamplePages":int(os.getenv("DOCUMENT_PRESCAN_MAX_PAGES","12")),
                        "budgetMs":int(os.getenv("DOCUMENT_PRESCAN_BUDGET_MS","2000"))},
            "parseModes": ["AUTO", "HIGH_ACCURACY", "FAST", "DOCLING", "DOCX_STREAM"]}

class ParseJob:
    def __init__(self, file, resume_pages=0, resume_batches=0, heading="", parse_mode="DOCLING", resume_state=""):
        cleanup_pending()
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".pdf", ".docx"}:
            file.file.close()
            raise HTTPException(422, "Only PDF and DOCX are supported")
        if (parse_mode not in {"AUTO", "HIGH_ACCURACY", "FAST", "DOCLING", "DOCX_STREAM"}
                or (parse_mode == "FAST" and suffix != ".pdf")
                or (parse_mode == "DOCX_STREAM" and suffix != ".docx")):
            file.file.close()
            raise HTTPException(422, "Invalid parsing mode")
        self.gate = fast_lock if parse_mode == "FAST" else docx_stream_lock if parse_mode == "DOCX_STREAM" else lock
        if len(resume_state.encode("utf-8")) > 256 * 1024:
            file.file.close()
            raise HTTPException(422, "Resume state exceeds limit")
        try:
            state = json.loads(resume_state) if resume_state else None
        except (ValueError, TypeError):
            file.file.close()
            raise HTTPException(422, "Invalid resume state JSON")
        if not self.gate.acquire(blocking=False):
            file.file.close()
            raise HTTPException(503, "Parser busy")
        self.directory = None
        self.closed = False
        self.events = None
        try:
            self.directory = tempfile.TemporaryDirectory(prefix="docling-")
            self.source = Path(self.directory.name) / ("input" + suffix)
            size = 0
            with self.source.open("wb") as output:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(413, "Document exceeds file limit")
                    output.write(chunk)
            if not size:
                raise HTTPException(422, "Empty document")
            self.settings = {"ocr_enabled": OCR_ENABLED, "ocr_languages": OCR_LANGUAGES, "max_pages": MAX_PAGES, "max_bytes": MAX_BYTES, "max_chars": MAX_TEXT_CHARS,
                "timeout": BATCH_TIMEOUT, "batch_pages": BATCH_PAGES, "resume_pages": resume_pages,
                "resume_batches": resume_batches, "heading": heading,
                "threads": max(1, int(os.getenv("OMP_NUM_THREADS", "4"))), "parse_mode": parse_mode, "table_mode": TABLE_MODE,
                "prescan_enabled": os.getenv("DOCUMENT_PRESCAN_ENABLED", "false").lower() == "true",
                "prescan_max_pages": int(os.getenv("DOCUMENT_PRESCAN_MAX_PAGES", "12")),
                "prescan_budget_ms": int(os.getenv("DOCUMENT_PRESCAN_BUDGET_MS", "2000")),
                "fast_batch_pages": max(1, min(100, int(os.getenv("DOCUMENT_FAST_BATCH_PAGES", "32")))),
                "resume_state": state, "continuity_enabled": os.getenv("DOCUMENT_CONTINUITY_ENABLED", "true").lower() == "true",
                "docx_batch_nodes": max(1, int(os.getenv("DOCUMENT_DOCX_BATCH_NODES", "32"))),
                "docx_batch_chars": max(1, int(os.getenv("DOCUMENT_DOCX_BATCH_CHARS", "12000"))),
                "docx_boundary_aware": os.getenv("DOCUMENT_DOCX_BOUNDARY_AWARE", "false").lower() == "true",
                "docx_boundary_lookahead_nodes": int(os.getenv("DOCUMENT_DOCX_BOUNDARY_LOOKAHEAD_NODES", "8")),
                "docx_max_node_chars": max(1, int(os.getenv("DOCUMENT_DOCX_MAX_NODE_CHARS", "200000"))),
                "docx_max_xml_bytes": max(1, int(os.getenv("DOCUMENT_DOCX_MAX_XML_BYTES", "134217728"))),
                "docx_max_metadata_bytes": max(1, int(os.getenv("DOCUMENT_DOCX_MAX_METADATA_BYTES", "8388608"))),
                "docx_max_table_cells": max(1, int(os.getenv("DOCUMENT_DOCX_MAX_TABLE_CELLS", "20000"))),
                "docx_max_image_bytes": max(1, int(os.getenv("DOCUMENT_DOCX_MAX_IMAGE_BYTES", "33554432"))),
                "docx_max_image_pixels": max(1, int(os.getenv("DOCUMENT_DOCX_MAX_IMAGE_PIXELS", "40000000"))),
                "docx_ocr_timeout": max(1, int(os.getenv("DOCUMENT_DOCX_OCR_TIMEOUT_SECONDS", "30"))),
                "docx_strict": os.getenv("DOCUMENT_DOCX_STREAM_STRICT", "true").lower() == "true"}
        except Exception:
            self.close()
            raise
        finally:
            file.file.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.events is not None:
            self.events.close()
        try:
            if self.directory:
                try:self.directory.cleanup()
                except OSError:
                    logging.warning("parser_temp_cleanup_deferred")
                    with _cleanup_lock:_cleanup_pending.append(self.directory)
        finally:
            self.gate.release()

    def stream(self):
        self.events = task_events(self.source, self.settings)
        try:
            for event in self.events:
                yield encode_event(event)
        finally:
            self.close()

@app.post("/parse-stream")
def parse_stream(file: UploadFile = File(...), resume_pages: int = Form(0, ge=0),
                 resume_batches: int = Form(0, ge=0), resume_heading: str = Form("", max_length=512),
                 parse_mode: str = Form("DOCLING"), resume_state: str = Form("", max_length=262144)):
    job = ParseJob(file, resume_pages, resume_batches, resume_heading, parse_mode, resume_state)
    return StreamingResponse(job.stream(), media_type="application/x-ndjson", background=BackgroundTask(job.close))


@app.post("/profile")
def profile(file: UploadFile = File(...)):
    """Internal dry-run: native preflight only, no models, indexing or writes."""
    from adaptive_pipeline import CAPABILITIES, profile_pdf, profile_docx, route_page
    from dataclasses import asdict
    job = ParseJob(file, parse_mode="AUTO")
    try:
        from prescan import isolated_prescan
        result = isolated_prescan(str(job.source), job.settings)
        for page in result.get("pages", []):
            parser, reasons = route_page(page)
            page["route"] = {"parser": parser, "reasons": reasons}
        return {"profile": result, "capabilities": [asdict(c) for c in CAPABILITIES],
                "chatLlmCalls": 0, "indexWrites": 0,
                "docxAutoExecution": "DOCLING_DOCUMENT_CHECKPOINT_COMPATIBILITY"}
    except TimeoutError as error:
        raise HTTPException(503,str(error)) from error
    except ValueError as error:
        raise HTTPException(422,str(error)) from error
    finally:
        job.close()

@app.post("/parse")
def parse(file: UploadFile = File(...)):
    job = ParseJob(file)
    result = {"blocks": [], "warnings": [], "partial": False}
    try:
        job.events = task_events(job.source, job.settings)
        for event in job.events:
            if event["type"] == "error":
                raise HTTPException(503 if event["retryable"] else 422, event["message"])
            if event["type"] == "batch":
                result["blocks"].extend(event["blocks"])
                result["warnings"].extend(event["warnings"])
        if not result["blocks"]:
            raise HTTPException(422, "No text found; check scan quality and OCR configuration")
        result["warnings"] = list(dict.fromkeys(result["warnings"]))
        return result
    finally:
        job.close()
