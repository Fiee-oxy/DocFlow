"""Isolated parse -> real HybridChunker/BGE/Milvus availability probe.

Uses a disposable collection only. SQL/MQ/upload are outside this measurement;
Java transaction/checkpoint behavior is covered separately by integration tests.
Never call this against the production collection. No chat model is invoked.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import uuid


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path("/work"))
    parser.add_argument("--key", default="zh_shenzhen")
    parser.add_argument("--milvus-uri", default="http://dovideo-milvus:19530")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    root = workspace / "evaluation/docx-stream-ab-20261027"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    entry = next(d for d in manifest["documents"] if d["key"] == args.key)
    source = workspace / entry["path"]
    collection = "docx_ab_probe_" + uuid.uuid4().hex[:16]
    os.environ["MILVUS_COLLECTION"] = collection
    os.environ["MILVUS_URI"] = args.milvus_uri
    os.environ["HF_HOME"] = "/models/huggingface"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    runner = module("docx_ab_parse_runner", workspace / "scripts/run-docx-stream-ab.py")
    retrieval = module("docx_ab_retrieval", Path("/retrieval/app.py"))
    from pipeline import task_events
    report = {"key": args.key, "llmCalls": 0, "collection": collection,
              "measurement": "production parser + HybridChunker + local BGE + temporary Milvus; excludes upload/MQ/SQL",
              "modes": {}, "cleanup": False}
    output = root / args.key / "index-probe.json"
    warmed = time.perf_counter()
    try:
        sample_vector = retrieval.encode(["Document index availability check"])[0]
        report["embedding_warmup_ms"] = round((time.perf_counter() - warmed) * 1000, 3)
        collection_started = time.perf_counter()
        retrieval.ensure_collection(len(sample_vector))
        report["collection_setup_ms"] = round((time.perf_counter() - collection_started) * 1000, 3)
        report["timing_conditions"] = "Embedding and disposable collection prepared before both mode timers; one pass, DOCLING then DOCX_STREAM"
        for mode in ("DOCLING", "DOCX_STREAM"):
            identifier = str(uuid.uuid4())
            settings = runner.base_settings()
            settings["parse_mode"] = mode
            settings["docx_strict"] = True
            row = {"status": "RUNNING", "batch_count": 0, "chunk_count": 0,
                   "first_index_visible_ms": None, "first_searchable_ms": None,
                   "index_work_ms": 0.0, "full_pipeline_ms": None, "batches": []}
            report["modes"][mode] = row
            started = time.perf_counter()
            first_request = None
            first_ids = None
            generator = task_events(str(source), settings)
            try:
                for event in generator:
                    if event["type"] == "batch":
                        batch_number = row["batch_count"]
                        received_ms = (time.perf_counter() - started) * 1000
                        request = retrieval.ChunkRequest(document_id=identifier, owner_id=991027,
                            index_version="docx-ab-v1", batch_index=batch_number, batch=event)
                        index_started = time.perf_counter()
                        indexed = retrieval.index(request)
                        index_ms = (time.perf_counter() - index_started) * 1000
                        chunks = indexed["chunks"]
                        row["index_work_ms"] += index_ms
                        row["batch_count"] += 1
                        row["chunk_count"] += len(chunks)
                        row["batches"].append({"batch": batch_number, "parse_event_ms": received_ms,
                            "index_ms": index_ms, "chunks": len(chunks),
                            "start_node": event.get("startNode"), "end_node": event.get("endNode")})
                        if chunks and first_request is None:
                            row["first_index_visible_ms"] = (time.perf_counter() - started) * 1000
                            first_request = request
                            first_ids = {c["chunk_id"] for c in chunks}
                            # Run an actual scoped dense+BM25 query before receiving later batches.
                            first = chunks[0]
                            result = retrieval.search(retrieval.SearchRequest(owner_id=991027,
                                scopes=[{"document_id": identifier, "index_version": "docx-ab-v1"}],
                                question=first["content"][:160], dense_top_k=8, sparse_top_k=8))
                            hits = result["dense"] + result["sparse"]
                            if not any(h["chunk_id"] in first_ids for h in hits):
                                raise RuntimeError("First published batch not returned by scoped search")
                            row["first_searchable_ms"] = (time.perf_counter() - started) * 1000
                    elif event["type"] == "error":
                        row.update(status="ERROR", error=event)
                        break
                    elif event["type"] == "complete":
                        row["status"] = "COMPLETE"
                        break
                if row["status"] == "RUNNING":
                    row["status"] = "INCOMPLETE_STREAM"
            finally:
                generator.close()
                row["full_pipeline_ms"] = (time.perf_counter() - started) * 1000
            if first_request is not None:
                # Retry outside timing: IDs must stay stable and scoped row count unchanged.
                scope = retrieval.scope_filter(retrieval.SearchRequest(owner_id=991027,
                    scopes=[{"document_id": identifier, "index_version": "docx-ab-v1"}], question="retry"))
                before = retrieval.milvus().query(collection_name=collection, filter=scope,
                    output_fields=["count(*)"], consistency_level="Strong")[0]["count(*)"]
                retried = retrieval.index(first_request)
                after = retrieval.milvus().query(collection_name=collection, filter=scope,
                    output_fields=["count(*)"], consistency_level="Strong")[0]["count(*)"]
                same_ids = first_ids == {c["chunk_id"] for c in retried["chunks"]}
                row["retry_idempotency"] = {"same_ids": same_ids,
                    "rows_before": before, "rows_after": after, "passed": before == after and same_ids}
            runner.write_json(output, report)
    except Exception as error:
        report["exception"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        try:
            client = retrieval.milvus()
            if client.has_collection(collection):
                client.drop_collection(collection)
            report["cleanup"] = True
        finally:
            runner.write_json(output, report)
    print(json.dumps(report, ensure_ascii=True), flush=True)
    if any(row["status"] != "COMPLETE" or not row.get("retry_idempotency", {}).get("passed")
           for row in report["modes"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
