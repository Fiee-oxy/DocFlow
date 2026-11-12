"""Read-only replay of frozen Docling conversions; no parser inference or LLM.

Run with .continuity-venv/Scripts/python.exe scripts/check-boundary-replay.py.
Only evaluation artifacts are written. Production documents and indexes are not
read or modified. Saved documents are validated/re-normalized with docling-core.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "docling"))
from boundary_stream import BoundaryAssembler
from continuity import source_fragments
from normalization import normalize
from docling_core.types.doc import DoclingDocument


DOCUMENTS = [
    ("perf-c6e7e73466012702", "zh"),
    ("perf-5a023e1dce750ab2", "en"),
]


def wire(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_event(raw, heading):
    original = deepcopy(raw["doclingDocument"])
    document = DoclingDocument.model_validate(deepcopy(original))
    parsed = normalize(document, list(heading), ocr_enabled=False)
    result = deepcopy(raw)
    result.update(parsed)
    # Preserve the converter's original structural document byte-for-byte as
    # JSON data. Only normalized blocks get today's heading/geometry metadata.
    result["doclingDocument"] = original
    assert result["doclingDocument"] == raw["doclingDocument"]
    return result, list(parsed["headingState"])


def settings_for(batch_pages, *, resume_pages=0, resume_batches=0, state=None):
    return {"parse_mode": "DOCLING", "batch_pages": batch_pages,
            "resume_pages": resume_pages, "resume_batches": resume_batches,
            "resume_state": state, "heading": "", "ocr_enabled": False,
            "ocr_languages": ["chi_sim", "eng"], "table_mode": "fast",
            "continuity_enabled": True}


def fragment_counter(blocks):
    return Counter(canonical(fragment) for block in blocks for fragment in source_fragments(block))


def body_cell_signature(cell):
    return canonical({"text": cell.get("text", ""),
                      "start_col": cell["start_col_offset_idx"],
                      "end_col": cell["end_col_offset_idx"],
                      "row_span": cell["end_row_offset_idx"] - cell["start_row_offset_idx"],
                      "row_header": bool(cell.get("row_header", False))})


def body_cells(block):
    return Counter(body_cell_signature(cell) for cell in block.get("table_data", {}).get("table_cells", [])
                   if not cell.get("column_header", False))


def evaluate_document(key, language, destination):
    started = time.perf_counter()
    source = ROOT / "evaluation" / "chunk-expanded-20261019" / "inputs" / key / "events.jsonl"
    before_hash = file_sha(source)
    saved = [json.loads(line) for line in source.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    raw_batches = [event for event in saved if event.get("type") == "batch" and event.get("doclingDocument")]
    assert raw_batches, f"No saved Docling batches in {source}"
    sizes = [event["endPage"] - event["startPage"] + 1 for event in raw_batches]
    batch_pages = Counter(sizes).most_common(1)[0][0]
    total = raw_batches[-1]["totalPages"]
    assert raw_batches[-1]["endPage"] == total
    assembler = BoundaryAssembler(settings_for(batch_pages))
    heading, normalized, output = [], [], []
    expected_fragments, committed = [], []
    checkpoints = []
    for index, raw in enumerate(raw_batches):
        batch, heading = normalize_event(raw, heading)
        for block in batch["blocks"]:
            block["batch_index"] = index
        expected_fragments.extend(source_fragments(block)[0] for block in batch["blocks"])
        normalized.append(deepcopy(batch))
        processed = wire(assembler.process(batch))
        assert processed["doclingDocument"] == raw["doclingDocument"], "Raw Docling JSON was changed"
        committed.extend(processed["blocks"])
        pending = [processed["resumeState"]["pending"]] if processed["resumeState"]["pending"] else []
        expected_counter = Counter(canonical(fragment) for fragment in expected_fragments)
        actual_counter = fragment_counter(committed + pending)
        assert actual_counter == expected_counter, f"Missing/duplicate/changed source fragments after batch {index}"
        checkpoints.append({"batch": index, "throughPage": raw["endPage"],
                            "originalSources": len(expected_fragments),
                            "committedSources": sum(len(source_fragments(block)) for block in committed),
                            "pendingSources": sum(len(source_fragments(block)) for block in pending),
                            "stateBytes": len(json.dumps(processed["resumeState"], ensure_ascii=False).encode("utf-8")),
                            "conservationPassed": True})
        output.append(processed)
    complete = {"type": "complete", "totalPages": total}
    assert wire(assembler.process(complete)) == complete
    assert assembler.pending is None
    # Replay *every* saved checkpoint through a real JSON serialization boundary.
    # Re-normalize subsequent raw documents using the restored heading, rather
    # than just feeding pre-normalized fixtures into the resumed assembler.
    for index, checkpoint in enumerate(output):
        state = wire(checkpoint["resumeState"])
        options = settings_for(batch_pages, resume_pages=state["parsedThroughPage"],
                               resume_batches=index + 1, state=state)
        resumed = BoundaryAssembler(options)
        heading = options["heading"].split(" / ") if options["heading"] else []
        resumed_events = []
        for raw in raw_batches[index + 1:]:
            event, heading = normalize_event(raw, heading)
            resumed_events.append(wire(resumed.process(event)))
        assert resumed_events == output[index + 1:], f"Resume differs after batch {index}"
        assert wire(resumed.process(complete)) == complete
        checkpoints[index]["resumeSubsequentBatches"] = len(resumed_events)
        checkpoints[index]["resumeExactlyMatched"] = True
    merged = [block for block in committed if len(source_fragments(block)) > 1]
    merges = []
    table_checks = []
    for block in merged:
        fragments = source_fragments(block)
        assert block.get("continuity"), "Multi-fragment block is missing merge reason"
        merges.append({"kind": block["continuity"]["kind"], "reason": block["continuity"]["reason"],
                       "pages": block["pages"], "section": block.get("section"),
                       "sourceCount": len(fragments),
                       "sources": [{"batch": part.get("batch_index"), "ref": part.get("ref"),
                                    "pages": part.get("pages"), "text": part.get("text", ""),
                                    "bbox": part.get("bbox"), "page_size": part.get("page_size")}
                                   for part in fragments],
                       "mergedText": block["text"],
                       "humanSemanticReview": "NOT_COMPLETED"})
        if block.get("label") == "table":
            before = sum((body_cells(part) for part in fragments), Counter())
            after = body_cells(block)
            assert before == after, "Table body cells were lost/duplicated during merge"
            table_checks.append({"pages": block["pages"], "sourceTables": len(fragments),
                                 "originalBodyCells": sum(before.values()),
                                 "mergedBodyCells": sum(after.values()), "bodyCellsPreserved": True})
    assert file_sha(source) == before_hash, "Source fixture was modified"
    result = {"documentId": key, "language": language, "name": raw_batches[0]["doclingDocument"].get("name"),
              "pages": total, "batchPages": batch_pages, "batches": len(raw_batches),
              "sourceSha256": before_hash, "originalNormalizedBlocks": len(expected_fragments),
              "originalTableBlocks": sum(part.get("label") == "table" for part in expected_fragments),
              "outputBlocks": len(committed), "outputOriginalFragments": sum(len(source_fragments(b)) for b in committed),
              "mergeOperations": sum(event["continuity"]["mergedBoundaries"] for event in output),
              "mergedParagraphGroups": sum(block.get("label") != "table" for block in merged),
              "mergedTableGroups": len(table_checks),
              "crossBatchMergeGroups": sum(len({p.get("batch_index") for p in source_fragments(b)}) > 1 for b in merged),
              "deferredBatches": sum(event["continuity"]["deferred"] for event in output),
              "canonicalDocumentBatches": sum("chunkingDocument" in event for event in output),
              "sourceConservationPassed": True, "rawDocumentsUnchanged": True,
              "checkpoints": checkpoints, "tableConservation": table_checks,
              "mergedGroups": merges,
              "offlineReplayElapsedSeconds": round(time.perf_counter() - started, 4)}
    (destination / f"{key}-replay.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # Detached outputs are useful for audits, without duplicating raw converter
    # JSON for each checkpoint replay. The original fixture remains the source.
    audit = [{"startPage": event["startPage"], "endPage": event["endPage"],
              "blocks": event["blocks"], "resumeState": event["resumeState"],
              "continuity": event["continuity"]} for event in output]
    (destination / f"{key}-processed.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evaluation/boundary-continuity-20261023")
    arguments = parser.parse_args()
    destination = (ROOT / arguments.output).resolve()
    allowed = (ROOT / "evaluation" / "boundary-continuity-20261023").resolve()
    if destination != allowed and allowed not in destination.parents:
        raise ValueError("Output must remain within evaluation/boundary-continuity-20261023")
    destination.mkdir(parents=True, exist_ok=True)
    documents = [evaluate_document(key, language, destination) for key, language in DOCUMENTS]
    summary = {"method": "FROZEN_DOCLING_JSON_RENORMALIZATION_AND_BOUNDARY_REPLAY",
               "llmCalls": 0, "parserInferenceCalls": 0, "embeddingCalls": 0,
               "writesToProduction": 0,
               "limitations": [
                   "Saved Docling batches are 4 pages, not the currently configured production batch size.",
                   "These fixtures yielded no cross-batch merges or pending checkpoints; positive carry recovery needs synthetic tests or another real document.",
                   "These fixtures yielded no merged table; original body-cell preservation checks have no positive real-table sample here.",
                   "Semantic correctness of the two paragraph merges has not received independent human annotation.",
                   "This is not a parser speed, retrieval recall or answer accuracy evaluation."],
               "codeSha256": {str(path.relative_to(ROOT)): file_sha(path) for path in [
                   ROOT / "services/docling/continuity.py", ROOT / "services/docling/boundary_stream.py",
                   ROOT / "services/docling/normalization.py", ROOT / "services/docling/chunking_document.py",
                   Path(__file__).resolve()]}, "documents": documents}
    (destination / "docling-replay-results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 冻结 Docling 文档的跨页续接回放", "",
             "本轮验证来源保留和检查点恢复，不评价解析准确率、RAG 召回率或真实解析速度。"
             "直接读取既有 Docling JSON，未重新执行解析、OCR、Embedding 或聊天 LLM，未修改生产文档和索引。", "",
             "两份文件实际保存的批次均为 4 页；中文文件最后一批为 1 页。本轮按原批次边界回放，没有改成 1 页批次。", "",
             "| 文档 | 页数 | 原始内容块 | 续接后块数 | 段落续接组 | 表格续接组 | 跨批续接组 | 检查点完全一致 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for item in documents:
        lines.append(f"| {item['language']} / {item['name']} | {item['pages']} | {item['originalNormalizedBlocks']} | "
                     f"{item['outputBlocks']} | {item['mergedParagraphGroups']} | {item['mergedTableGroups']} | "
                     f"{item['crossBatchMergeGroups']} | {len(item['checkpoints'])}/{len(item['checkpoints'])} |")
    lines.extend(["", "所有非空原始内容块逐字段对比均在提交结果或 pending 中恰好保留一次；"
                  "比较包含完整原文、页码、引用、坐标和原始表格数据。每个批次检查点经过 JSON 序列化后恢复，"
                  "重新归一化剩余原始文档，后续事件与不中断执行完全一致，包含最终检查点。", "",
                  "合并表格逐个比较正文单元格的文本、列位置、行跨度与行表头标志；重复列头仅从合并视图删除，"
                  "原始单元格与来源仍保留在 source_fragments。若表格续接数为 0，则本样本没有提供真实表格合并的正例。", "",
                  "本轮真实样本没有跨批次合并，也没有 pending 检查点；7 个恢复点均一致，但不能据此声称已在真实文档上"
                  "验证带待续内容的断点恢复。该路径仍需依靠合成用例，或另选实际存在对应边界的文件验证。", "",
                  "## 需人工核对的实际合并边界", "",
                  "以下合并仅通过保守规则；未完成人工语义标注，不将规则通过等同于正确合并。"])
    for item in documents:
        lines.extend(["", f"### {item['language']} / {item['name']}", ""])
        if not item["mergedGroups"]:
            lines.append("本文件没有触发合并，不声称其跨页问题已得到改善。")
        for group in item["mergedGroups"]:
            snippets = [part["text"][-180:] if index == 0 else part["text"][:180]
                        for index, part in enumerate(group["sources"])]
            lines.append(f"- 页 {group['pages']}；{group['kind']}；规则：{group['reason']}。")
            for snippet in snippets:
                lines.append("  - `" + snippet.replace("`", "'").replace("\n", " ") + "`")
    lines.extend(["", "完整合并前后原文和检查点指标见 docling-replay-results.json；"
                  "逐批提交结果与 pending 见对应的 *-processed.json。", ""])
    (destination / "docling-replay-report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"documents": [{k: d[k] for k in ["documentId", "pages", "originalNormalizedBlocks", "outputBlocks",
                                                      "mergedParagraphGroups", "mergedTableGroups", "crossBatchMergeGroups"]}
                                    for d in documents], "checkpointsPassed": sum(len(d["checkpoints"]) for d in documents),
                      "llmCalls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
