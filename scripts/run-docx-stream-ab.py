"""Isolated parse-only benchmark using production task_events, never production DB.

Run inside the dedicated parser container with this repository at /workspace.
Original DOCX inputs are read-only; mode failures and strict rejections remain
failures in run.json.  No fallback, embedding, indexing, or chat LLM is invoked.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import time
import traceback

WORKSPACE = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for part in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def resolve(path, manifest_directory):
    path = Path(path)
    for candidate in (path, WORKSPACE / path, manifest_directory / path):
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"Source file not found: {path}")


def base_settings(timeout=600, batch_nodes=32, batch_chars=12000):
    return {"ocr_enabled": True, "ocr_languages": ["chi_sim", "eng"],
            "max_pages": 1000, "max_bytes": 209715200, "max_chars": 10000000,
            "timeout": timeout, "batch_pages": 4, "fast_batch_pages": 32,
            "resume_pages": 0, "resume_batches": 0, "resume_state": None,
            "heading": "", "threads": 4, "table_mode": "fast", "continuity_enabled": True,
            "docx_batch_nodes": batch_nodes, "docx_batch_chars": batch_chars,
            "docx_strict": True}


def collect(source, settings, events_path, task_events):
    """Save the exact production event stream; timings never modify its fields."""
    events_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    result = {"status": "RUNNING", "first_batch_ms": None, "first_nonempty_batch_ms": None,
              "total_ms": None, "batch_count": 0, "block_count": 0, "event_count": 0,
              "settings": deepcopy(settings), "llmCalls": 0,
              "timing_basis": "production task_events subprocess startup + conversion + NDJSON persistence; excludes upload/MQ/SQL/index/retrieval/LLM"}
    batches = []
    unsupported = False
    generator = None
    try:
        with events_path.open("w", encoding="utf-8", newline="\n") as output:
            generator = task_events(str(source), deepcopy(settings))
            for event in generator:
                output.write(json.dumps(event, ensure_ascii=False) + "\n")
                output.flush()
                result["event_count"] += 1
                kind = event.get("type")
                unsupported = unsupported or bool(event.get("partial")) or any("DOCX_UNSUPPORTED_" in str(w) for w in event.get("warnings", []))
                if kind == "batch":
                    elapsed = (time.perf_counter() - started) * 1000
                    if result["first_batch_ms"] is None:
                        result["first_batch_ms"] = elapsed
                    if event.get("blocks") and result["first_nonempty_batch_ms"] is None:
                        result["first_nonempty_batch_ms"] = elapsed
                    result["batch_count"] += 1
                    result["block_count"] += len(event.get("blocks", []))
                    # Keep only persisted cursors in harness memory; full source
                    # batches are already durable in the NDJSON artifact.
                    batches.append({"resumeState": deepcopy(event.get("resumeState") or event.get("docxResumeState"))})
                elif kind == "error":
                    result["error"] = deepcopy(event)
                    result["status"] = "REJECTED_UNSUPPORTED" if "DOCX_UNSUPPORTED_" in event.get("message", "") else "ERROR"
                    break
                elif kind == "complete":
                    result["status"] = "PARTIAL_UNSUPPORTED" if unsupported else "COMPLETE"
                    break
            if result["status"] == "RUNNING":
                result["status"] = "INCOMPLETE_STREAM"
    except Exception as error:
        result["status"] = "EXCEPTION"
        result["exception_type"] = type(error).__name__
        result["exception"] = str(error)
        result["traceback"] = traceback.format_exc()
    finally:
        if generator is not None and hasattr(generator, "close"):
            try:
                generator.close()
            except Exception as error:
                result["status"] = "EXCEPTION"
                result["cleanup_exception"] = f"{type(error).__name__}: {error}"
        result["total_ms"] = (time.perf_counter() - started) * 1000
    return result, batches


def checkpoint_counts(total):
    if total < 1:
        return []
    counts = {}
    for label, count in (("first", 1), ("middle", max(1, total // 2)), ("end", total)):
        counts.setdefault(count, []).append(label)
    return [{"afterBatch": count, "positions": labels} for count, labels in sorted(counts.items())]


def resume_runs(source, settings, folder, batches, task_events, save_progress, mode="stream"):
    checks = []
    for checkpoint in checkpoint_counts(len(batches)):
        count = checkpoint["afterBatch"]
        state = batches[count - 1].get("resumeState") or batches[count - 1].get("docxResumeState")
        if not isinstance(state, dict):
            checks.append({"mode": mode, **checkpoint, "status": "MISSING_CHECKPOINT"})
            save_progress(checks)
            continue
        # JSON round-trip models checkpoint persistence, rather than retaining
        # mutable in-process parser objects.
        state = json.loads(json.dumps(state, ensure_ascii=False))
        resumed = deepcopy(settings)
        resumed.update(resume_state=state, resume_pages=0, resume_batches=count,
                       heading=" / ".join(state.get("heading", [])))
        filename = f"{mode}-resume-{count}-events.jsonl"
        result, _ = collect(source, resumed, folder / filename, task_events)
        checks.append({"mode": mode, **checkpoint, "eventsPath": filename,
                       "includesPrefix": False, "status": result["status"], "measurement": result})
        save_progress(checks)
    return checks


def runtime_info():
    versions = {}
    for name in ("docling", "docling-core", "pypdfium2", "torch"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"python": platform.python_version(), "platform": platform.platform(),
            "packages": versions, "logical_cpu_count": os.cpu_count(),
            "OMP_NUM_THREADS": os.getenv("OMP_NUM_THREADS"),
            "model_network_mode": {key: os.getenv(key) for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")},
            "code_sha256": {name: sha256(WORKSPACE / name) for name in
                            ("services/docling/pipeline.py", "services/docling/docx_stream.py",
                             "services/docling/normalization.py", "services/docling/chunking_document.py",
                             "scripts/run-docx-stream-ab.py") if (WORKSPACE / name).is_file()},
            "basis": "Dedicated isolated parser container; production task_events; serial documents/modes; no explicit warm-up"}


def run_document(item, index, settings, args, task_events, runtime):
    key = item.get("key", item.get("id"))
    if not key or Path(key).name != key:
        raise ValueError("Invalid document key")
    source = resolve(item.get("path", item.get("file")), args.manifest.parent)
    actual_sha = sha256(source)
    declared_sha = item.get("sha256", item.get("source_sha256"))
    if declared_sha and declared_sha != actual_sha:
        raise ValueError(f"SHA-256 mismatch for {key}")
    folder = args.root / key
    folder.mkdir(parents=True, exist_ok=True)
    selected = list(args.modes)
    if index % 2:
        selected.reverse()
    if args.stream_text_only_diagnostic:
        selected.append("stream_text_only")
    filename_mode = lambda mode: "stream-text-only" if mode == "stream_text_only" else mode
    for mode in selected:
        if (folder / f"{filename_mode(mode)}-events.jsonl").exists() and not args.overwrite:
            raise FileExistsError(f"Refusing to replace {key}/{mode}-events.jsonl; use --overwrite for an intentional rerun")
    run_path = folder / "run.json"
    run = json.loads(run_path.read_text(encoding="utf-8-sig")) if run_path.exists() else {}
    run.update(document=key, kind=item.get("kind", "REAL"), language=item.get("language"),
               source={"path": str(source), "sha256": actual_sha, "bytes": source.stat().st_size},
               mode_order=selected, runtime=runtime, llmCalls=0, defaultChanged=False)
    write_json(run_path, run)
    for mode in selected:
        configured = deepcopy(settings)
        configured["parse_mode"] = "DOCLING" if mode == "docling" else "DOCX_STREAM"
        configured["docx_boundary_aware"] = mode == "boundary"
        if mode == "stream_text_only":
            configured["docx_strict"] = False
        measured, batches = collect(source, configured, folder / f"{filename_mode(mode)}-events.jsonl", task_events)
        if mode == "stream_text_only":
            measured.update(diagnostic_only=True, eligible_for_ingestion=False,
                            scope="TEXT_AND_TABLE_DIAGNOSTIC_WITH_EXPLICIT_UNSUPPORTED_LOSS")
        run[mode] = measured
        write_json(run_path, run)
        print(json.dumps({"document": key, "mode": mode, "status": measured["status"],
                          "first_batch_ms": measured["first_batch_ms"], "total_ms": measured["total_ms"],
                          "batches": measured["batch_count"], "blocks": measured["block_count"],
                          "error": measured.get("error")}, ensure_ascii=True), flush=True)
        if mode in {"stream", "boundary"}:
            checkpoint_key="resumeChecks" if mode == "stream" else "boundaryResumeChecks"
            run[checkpoint_key] = []
            if measured["status"] == "COMPLETE" and args.resume:
                def persist(checks):
                    run[checkpoint_key] = deepcopy(checks)
                    write_json(run_path, run)
                run[checkpoint_key] = resume_runs(source, configured, folder, batches, task_events, persist, mode=mode)
                write_json(run_path, run)
    return run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=WORKSPACE / "evaluation/docx-stream-ab-20261027")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--modes", nargs="+", choices=("docling", "stream", "boundary"), default=["docling", "stream"])
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--batch-nodes", type=int, default=32)
    parser.add_argument("--batch-chars", type=int, default=12000)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--stream-text-only-diagnostic", action="store_true",
                        help="Additional separately named non-strict diagnostic; never replaces strict stream")
    args = parser.parse_args()
    args.root = args.root.resolve()
    args.manifest = (args.manifest or args.root / "manifest.json").resolve()
    if args.timeout_seconds < 10 or args.batch_nodes < 1 or args.batch_chars < 1:
        parser.error("positive batch limits and timeout >=10 seconds required")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    settings = base_settings(args.timeout_seconds, args.batch_nodes, args.batch_chars)
    # The manifest may freeze parser size limits, but cannot silently disable
    # strictness, select a fallback, or inject resume state into this full run.
    allowed = {k: v for k, v in manifest.get("settings", {}).items() if
               k not in {"parse_mode", "docx_strict", "resume_state", "docx_resume_state", "resume_pages", "resume_batches", "heading"}}
    settings.update(allowed)
    settings["docx_strict"] = True
    sys.path.insert(0, str(WORKSPACE / "services/docling"))
    from pipeline import task_events
    runtime = runtime_info()
    documents = manifest["documents"]
    if args.only:
        requested = set(args.only)
        documents = [d for d in documents if d.get("key", d.get("id")) in requested]
        if len(documents) != len(requested):
            parser.error("Unknown --only document key")
    rows = []
    for index, item in enumerate(documents):
        rows.append(run_document(item, index, settings, args, task_events, runtime))
    executed_modes = list(args.modes) + (["stream_text_only"] if args.stream_text_only_diagnostic else [])
    unexpected = [{"document": row["document"], "mode": mode, "status": row[mode]["status"]}
                  for row in rows for mode in executed_modes
                  if row.get(mode, {}).get("status") not in ({"COMPLETE", "REJECTED_UNSUPPORTED", "PARTIAL_UNSUPPORTED"} if mode == "stream_text_only" else {"COMPLETE", "REJECTED_UNSUPPORTED"})]
    failed_resume = [{"document": row["document"], "afterBatch": check["afterBatch"], "status": check["status"]}
                     for row in rows for check in row.get("resumeChecks", []) if check["status"] != "COMPLETE"]
    write_json(args.root / "execution-summary.json", {"llmCalls": 0, "documents": len(rows),
                                                       "unexpected_failures": unexpected, "failed_resume_runs": failed_resume,
                                                       "note": "Strict unsupported rejections are recorded as rejections, never parser successes; scorer checks exact resume equivalence"})
    if unexpected or failed_resume:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
