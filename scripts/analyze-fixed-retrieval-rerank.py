"""Analyze a frozen no-LLM retrieval run without issuing any API request."""
import importlib.util
import argparse
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--run-dir", default="evaluation/rag-quality/outputs/fixed-retrieval-rerank-20261015")
args = parser.parse_args()
RUN = (ROOT / args.run_dir).resolve()
if (ROOT / "evaluation/rag-quality/outputs").resolve() not in RUN.parents:
    parser.error("run directory must be under evaluation/rag-quality/outputs")
EXPORTS = ROOT / "evaluation/rag-quality/outputs/recall-20261014/documents"

spec = importlib.util.spec_from_file_location("coverage", ROOT / "scripts/rag-coverage.py")
coverage_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage_module)

cases = json.loads((RUN / "cases.json").read_text(encoding="utf-8"))
case_by_id = {case["case_id"]: case for case in cases}
lookups = {}
for key in {case["document_key"] for case in cases}:
    chunks = json.loads((EXPORTS / key / "rag_chunks.json").read_text(encoding="utf-8"))
    lookups[key] = {row["chunk_id"]: row.get("blockIndex", row.get("block_index", i)) for i, row in enumerate(chunks)}

def complete(rows, case):
    return coverage_module.coverage(rows, case, lookups[case["document_key"]])["complete"]

def first_complete_rank(rows, case):
    for rank in range(1, len(rows) + 1):
        if complete(rows[:rank], case):
            return rank
    return None

rows = []
for case in cases:
    preview = json.loads((RUN / "previews" / f"{case['case_id']}.json").read_text(encoding="utf-8"))
    trace = preview["retrieval"][0]
    dense, sparse = trace.get("dense", []), trace.get("bm25", [])
    cap_results = {}
    for cap in (10, 20, 30, 40):
        cap_results[str(cap)] = complete(dense[:cap] + sparse[:cap], case)
    rrf_rank = first_complete_rank(trace.get("rrf", []), case)
    rerank_rank = first_complete_rank(trace.get("reranked", []), case)
    rows.append({
        "case_id": case["case_id"], "language": case["language"], "question": case["question"],
        "candidate_complete": cap_results, "rrf_first_complete_rank": rrf_rank,
        "reranked_first_complete_rank": rerank_rank,
        "reranker_effect": "missing" if rrf_rank is None or rerank_rank is None else
            "improved" if rerank_rank < rrf_rank else "regressed" if rerank_rank > rrf_rank else "tied"
    })

def mrr(field):
    return sum(0 if row[field] is None else 1 / row[field] for row in rows) / len(rows)

summary = {
    "fixed_parameters": json.loads((RUN / "runtime.json").read_text(encoding="utf-8")),
    "cases": len(rows),
    "candidate_recall": {cap: sum(row["candidate_complete"][cap] for row in rows) for cap in ("10", "20", "30", "40")},
    "candidate_recall_by_language": {
        lang: {cap: sum(row["language"] == lang and row["candidate_complete"][cap] for row in rows)
               for cap in ("10", "20", "30", "40")} for lang in ("zh", "en")
    },
    "ranking": {
        "rrf_mrr": round(mrr("rrf_first_complete_rank"), 4),
        "reranker_mrr": round(mrr("reranked_first_complete_rank"), 4),
        "improved": sum(row["reranker_effect"] == "improved" for row in rows),
        "tied": sum(row["reranker_effect"] == "tied" for row in rows),
        "regressed": sum(row["reranker_effect"] == "regressed" for row in rows),
        "missing": sum(row["reranker_effect"] == "missing" for row in rows),
        "rrf_complete_at": {str(k): sum(row["rrf_first_complete_rank"] is not None and row["rrf_first_complete_rank"] <= k for row in rows) for k in (1, 3, 5, 8, 12, 20, 28)},
        "reranker_complete_at": {str(k): sum(row["reranked_first_complete_rank"] is not None and row["reranked_first_complete_rank"] <= k for row in rows) for k in (1, 3, 5, 8, 12, 20, 28)},
    },
    "latency_seconds": {
        "median": statistics.median(json.loads((RUN / "stage-results.json").read_text(encoding="utf-8"))[i]["latencyMs"] / 1000 for i in range(len(rows)))
    },
    "no_llm_audit": json.loads((RUN / "no-llm-audit.json").read_text(encoding="utf-8")),
    "regressions": [row for row in rows if row["reranker_effect"] == "regressed"],
    "missing_after_fusion": [row for row in rows if row["reranker_effect"] == "missing"],
}
(RUN / "retrieval-reranker-analysis.json").write_text(json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
