"""Score frozen/control/continuity parser outputs without inference or an LLM.

The old parser output is a comparison baseline, never an accuracy gold. Table
relations are scored against existing visually checked labels. Every metric
exposes its denominator, unsupported spans and unreviewed boundaries.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
import unicodedata


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "evaluation/boundary-quality-20261026"
DOC_KEYS = ("perf-c6e7e73466012702", "perf-5a023e1dce750ab2")
SOURCE_KEYS = ("text", "pages", "ref", "label", "section", "bbox", "page_size", "prov",
               "table_data", "heading_level", "enumerated", "marker", "caption", "batch_index")


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def events_at(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def norm(value):
    """Atom matching tolerates typography, whitespace and punctuation only."""
    return re.sub(r"[^\w\u3400-\u9fff]+", "", unicodedata.normalize("NFKC", str(value)).casefold())


def normalized_prose(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value)))


def source_records(events):
    records = []
    batch_index = 0
    for event in events:
        if event.get("type") != "batch":
            continue
        raw = event.get("doclingDocument") or {}
        originals = {item.get("self_ref"): item for category in ("texts", "tables", "pictures")
                     for item in raw.get(category, [])}
        for block in event.get("blocks", []):
            for source in block.get("source_fragments") or [block]:
                record = deepcopy(source)
                record.setdefault("batch_index", batch_index)
                # Kept separately: comparison never invents fields in originals.
                record["_fallback_item"] = originals.get(source.get("ref"), {})
                record["_raw_pages"] = raw.get("pages", {})
                records.append(record)
        batch_index += 1
    return records


def per_page_text(records):
    text = defaultdict(list)
    ambiguous = []
    for record in records:
        pages = record.get("pages", [])
        if len(pages) != 1:
            ambiguous.append({"ref": record.get("ref"), "pages": pages,
                              "characters": len(record.get("text", ""))})
            continue
        text[pages[0]].append(record.get("text", ""))
    return {page: "\n".join(parts) for page, parts in text.items()}, ambiguous


def text_delta(before, after, total_pages):
    bp, b_ambiguous = per_page_text(before)
    ap, a_ambiguous = per_page_text(after)
    rows = []
    for page in range(1, total_pages + 1):
        a, b = normalized_prose(bp.get(page, "")), normalized_prose(ap.get(page, ""))
        matched = sum(size for _, _, size in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
        rows.append({"page": page, "beforeNormalizedCharacters": len(a), "afterNormalizedCharacters": len(b),
                     "sameNormalizedText": a == b, "orderedMatchingCharacters": matched,
                     "beforeCharactersRetainedFraction": matched / len(a) if a else None,
                     "afterCharactersSharedFraction": matched / len(b) if b else None})
    before_chars = sum(row["beforeNormalizedCharacters"] for row in rows)
    after_chars = sum(row["afterNormalizedCharacters"] for row in rows)
    matching_chars = sum(row["orderedMatchingCharacters"] for row in rows)
    return {"interpretation": "OUTPUT_DIFFERENCE_NOT_SOURCE_ACCURACY",
            "exactlyEqualPages": sum(row["sameNormalizedText"] for row in rows), "pagesDenominator": total_pages,
            "orderedMatchingCharacters": matching_chars, "beforeCharactersDenominator": before_chars,
            "afterCharactersDenominator": after_chars,
            "beforeMultiPageOrUnlocatedSourcesExcluded": b_ambiguous,
            "afterMultiPageOrUnlocatedSourcesExcluded": a_ambiguous, "pages": rows}


def atoms_score(records, cases):
    scores = []
    for case in cases:
        units = []
        for unit in case.get("units", []):
            pages = set(unit.get("pages", []))
            sources = [record for record in records if pages.intersection(record.get("pages", []))]
            # Full page-assigned text preserves order. It does not imply that
            # related fragments will land in the same retrieval chunk.
            text = norm("\n".join(record.get("text", "") for record in sources))
            atoms = [{"atom": atom, "found": bool(norm(atom)) and norm(atom) in text}
                     for atom in unit.get("atoms", [])]
            units.append({"pages": sorted(pages), "atoms": atoms,
                          "complete": bool(atoms) and all(atom["found"] for atom in atoms)})
        scores.append({"caseId": case["case_id"], "question": case["question"],
                       "originalLabelStatus": case.get("label_status", "SOURCE_ALIGNED_PENDING_HUMAN_REVIEW"),
                       "sourcePageReview": "CODEX_VISUALLY_CHECKED_NOT_INDEPENDENT_HUMAN_HELDOUT",
                       "units": units, "complete": bool(units) and all(unit["complete"] for unit in units)})
    atoms = [atom for case in scores for unit in case["units"] for atom in unit["atoms"]]
    return {"completeCases": sum(case["complete"] for case in scores), "casesDenominator": len(scores),
            "foundAtoms": sum(atom["found"] for atom in atoms), "atomsDenominator": len(atoms),
            "interpretation": "SAMPLED_NECESSARY_TEXT_RETENTION_NOT_ANSWER_ACCURACY", "cases": scores}


def geometry(records, total_pages):
    located = valid_boxes = invalid_boxes = missing = valid_page_sources = 0
    issues = []
    for index, record in enumerate(records):
        pages = record.get("pages", [])
        valid_pages = bool(pages) and all(isinstance(p, int) and not isinstance(p, bool)
                                          and 1 <= p <= total_pages for p in pages)
        valid_page_sources += valid_pages
        fallback = record["_fallback_item"]
        prov = record.get("prov") or fallback.get("prov", [])
        boxes = [{"page_no": pages[0], "bbox": record["bbox"]}] if record.get("bbox") and len(pages) == 1 else prov
        if not boxes:
            missing += 1
            continue
        source_ok = False
        for entry in boxes:
            box, page = entry.get("bbox"), entry.get("page_no")
            size = record.get("page_size") or record["_raw_pages"].get(str(page), {}).get("size")
            okay = False
            try:
                l, t, r, b = (float(box[k]) for k in ("l", "t", "r", "b"))
                w, h = float(size["width"]), float(size["height"])
                origin = str(box.get("coord_origin", "")).upper()
                finite = all(math.isfinite(n) for n in (l, t, r, b, w, h))
                ordered_y = t <= b if origin == "TOPLEFT" else t >= b if origin == "BOTTOMLEFT" else False
                # Small decimal rounding at a page boundary is harmless.
                okay = (finite and w > 0 and h > 0 and -.01 <= l <= r <= w + .01
                        and -.01 <= min(t, b) <= max(t, b) <= h + .01 and ordered_y
                        and isinstance(page, int) and 1 <= page <= total_pages)
            except (KeyError, TypeError, ValueError):
                pass
            valid_boxes += okay
            invalid_boxes += not okay
            source_ok |= okay
            if not okay and len(issues) < 40:
                issues.append({"sourceIndex": index, "ref": record.get("ref"), "pages": pages,
                               "box": box, "page": page, "size": size})
        located += valid_pages and source_ok
    return {"sourcesDenominator": len(records), "validPageSources": valid_page_sources,
            "sourcesWithAtLeastOneValidBox": located, "validBoxes": valid_boxes, "invalidBoxes": invalid_boxes,
            "boxesDenominator": valid_boxes + invalid_boxes, "sourcesWithoutBoxes": missing,
            "invalidExamples": issues, "interpretation": "COORDINATE_VALIDITY_NOT_VISUAL_ALIGNMENT_ACCURACY"}


def tables_from(events, page):
    tables = []
    for batch, event in enumerate(e for e in events if e.get("type") == "batch"):
        representation = "chunkingDocument" if event.get("chunkingDocument") is not None else "doclingDocument"
        document = event.get(representation) or {}
        for table in document.get("tables", []):
            pages = sorted({p.get("page_no") for p in table.get("prov", []) if p.get("page_no")})
            if page in pages:
                tables.append({"batch": batch, "representation": representation, "ref": table.get("self_ref"),
                               "pages": pages, "data": table.get("data", {})})
    return tables


def row_label(text):
    return norm(re.sub(r"\([^)]*\)", "", unicodedata.normalize("NFKC", str(text))))


def number(text):
    value = unicodedata.normalize("NFKC", str(text)).strip().replace(",", "")
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def table_relation_score(events, gold):
    candidates = []
    for table in tables_from(events, gold["page"]):
        cells = table["data"].get("table_cells", [])
        columns = {}
        for name in gold["column_order"]:
            matches = [cell for cell in cells if cell.get("column_header")
                       and cell.get("end_col_offset_idx", 0) - cell.get("start_col_offset_idx", 0) == 1
                       and norm(cell.get("text", "")) == norm(name)]
            if len(matches) == 1:
                columns[name] = matches[0]["start_col_offset_idx"]
        rows = {}
        for name, *_ in gold["rows"]:
            matches = [cell for cell in cells if row_label(cell.get("text", "")) == row_label(name)
                       and not cell.get("column_header")]
            if len(matches) == 1:
                rows[name] = matches[0]["start_row_offset_idx"]
        candidates.append({**table, "columns": columns, "rows": rows,
                           "locatorScore": len(columns) + len(rows)})
    candidates.sort(key=lambda item: item["locatorScore"], reverse=True)
    selected = candidates[0] if candidates and candidates[0]["locatorScore"] else None
    ambiguous = bool(selected and len(candidates) > 1 and candidates[1]["locatorScore"] == selected["locatorScore"])
    if ambiguous:
        selected = None
    relations = []
    for row_name, *values in gold["rows"]:
        for column_name, expected in zip(gold["column_order"], values):
            found = None
            reason = "TABLE_NOT_LOCATED" if not selected else "HEADER_OR_ROW_NOT_LOCATED"
            if selected and row_name in selected["rows"] and column_name in selected["columns"]:
                r, c = selected["rows"][row_name], selected["columns"][column_name]
                matches = [cell for cell in selected["data"].get("table_cells", [])
                           if cell.get("start_row_offset_idx", -1) <= r < cell.get("end_row_offset_idx", -1)
                           and cell.get("start_col_offset_idx", -1) <= c < cell.get("end_col_offset_idx", -1)]
                if len(matches) != 1:
                    reason = "MISSING_OR_OVERLAPPING_CELL"
                else:
                    found = matches[0]
                    if (found["end_row_offset_idx"] - found["start_row_offset_idx"] != 1 or
                            found["end_col_offset_idx"] - found["start_col_offset_idx"] != 1):
                        reason = "AMBIGUOUS_SPANNING_NUMERIC_CELL"
                    else:
                        reason = "MATCH" if number(found.get("text")) is not None and number(found["text"]) == number(expected) else "VALUE_MISMATCH"
            relations.append({"row": row_name, "column": column_name, "expected": expected,
                              "actual": found.get("text") if found else None, "correct": reason == "MATCH", "reason": reason})
    rows_complete = sum(all(relation["correct"] for relation in relations if relation["row"] == name)
                        for name, *_ in gold["rows"])
    return {"goldBasis": "VISUALLY_CHECKED_SELECTED_ROW_COLUMN_RELATIONSHIPS",
            "page": gold["page"], "name": gold["name"],
            "correctRelations": sum(relation["correct"] for relation in relations), "relationsDenominator": len(relations),
            "completeRows": rows_complete, "rowsDenominator": len(gold["rows"]),
            "locatedColumns": len(selected["columns"]) if selected else 0,
            "columnsDenominator": len(gold["column_order"]), "candidateTables": len(candidates),
            "ambiguousCandidateTie": ambiguous,
            "selected": {k: selected[k] for k in ("batch", "representation", "ref", "pages")} if selected else None,
            "relations": relations, "interpretation": "SELECTED_TABLE_FACTS_NOT_FULL_TEDS_OR_TABLE_ACCURACY"}


def continuation_groups(events):
    groups = []
    for event in events:
        for block in event.get("blocks", []):
            fragments = block.get("source_fragments", [])
            if len(fragments) > 1 and block.get("continuity"):
                groups.append({"kind": block["continuity"].get("kind"), "reason": block["continuity"].get("reason"),
                               "pages": block.get("pages"), "section": block.get("section"),
                               "crossBatch": len({part.get("batch_index") for part in fragments}) > 1,
                               "metadata": block["continuity"], "text": block.get("text"),
                               "sources": [{key: part[key] for key in SOURCE_KEYS if key in part} for part in fragments],
                               "semanticCorrectness": "PENDING_VISUAL_REVIEW"})
    return groups


def source_conservation(raw_events, repaired_events):
    def counter(events):
        return Counter(stable({key: value for key, value in record.items()
                               if not key.startswith("_") and key not in {"origin_id", "continuity", "source_fragments"}})
                       for record in source_records(events))
    before, after = counter(raw_events), counter(repaired_events)
    missing, extra = before - after, after - before
    return {"basis": "SAME_CONVERSION_BEFORE_AND_AFTER_BOUNDARY_ASSEMBLY",
            "originalSourcesDenominator": sum(before.values()), "preservedExactOriginalSources": sum((before & after).values()),
            "missingOrChangedSources": sum(missing.values()), "extraOrDuplicatedSources": sum(extra.values()),
            "exactConservation": before == after,
            "missingExamples": [json.loads(value) for value in list(missing)[:5]],
            "extraExamples": [json.loads(value) for value in list(extra)[:5]],
            "interpretation": "LOSSLESSNESS_NOT_SEMANTIC_CORRECTNESS"}


def run_timing(folder, name):
    paths = [folder / f"{name}-run.json", folder / name / "run.json"]
    if name == "new":
        paths.append(folder / "run.json")
    for path in paths:
        if path.exists():
            record = load(path)
            return {"source": str(path.relative_to(ROOT)), "record": record}
    return None


def assembly_ablation(raw_events, settings, repetitions=3):
    """Time only boundary assembly on the exact same saved normalization."""
    sys.path.insert(0, str(ROOT / "services/docling"))
    from boundary_stream import BoundaryAssembler
    settings = deepcopy(settings or {})
    settings.update(parse_mode="DOCLING", resume_pages=0, resume_batches=0, resume_state=None, heading="")
    # docling-core's first lazy import is not steady-state assembly overhead.
    # The live parser already imported that module while creating the raw input.
    warm_started = time.perf_counter()
    warm = BoundaryAssembler(dict(settings, continuity_enabled=True))
    warm_output = [warm.process(event) for event in deepcopy(raw_events)]
    if warm_output and warm_output[-1].get("type") != "complete":
        warm.process({"type": "complete", "totalPages": warm_output[-1].get("totalPages")})
    warmup_ms = (time.perf_counter() - warm_started) * 1000
    times = {"disabled": [], "enabled": []}
    counts = {}
    for repetition in range(repetitions):
        for mode in (("disabled", "enabled") if repetition % 2 == 0 else ("enabled", "disabled")):
            inputs = deepcopy(raw_events)
            options = dict(settings, continuity_enabled=mode == "enabled")
            start = time.perf_counter()
            assembler = BoundaryAssembler(options)
            output = [assembler.process(event) for event in inputs]
            if output and output[-1].get("type") != "complete":
                assembler.process({"type": "complete", "totalPages": output[-1].get("totalPages")})
            times[mode].append((time.perf_counter() - start) * 1000)
            counts[mode] = sum(len(event.get("blocks", [])) for event in output)
    return {"basis": "SAME_RAW_CONVERSION_BOUNDARY_ASSEMBLY_ONLY_NO_INFERENCE_OR_PERSISTENCE",
            "warmupExcludedMilliseconds": warmup_ms,
            "repetitions": repetitions, "inputBlockCount": sum(len(event.get("blocks", [])) for event in raw_events),
            "modes": {name: {"milliseconds": values, "meanMilliseconds": sum(values)/len(values),
                             "minMilliseconds": min(values), "maxMilliseconds": max(values),
                             "outputBlocks": counts[name]} for name, values in times.items()}}


def boundary_gold_score(events, labels):
    """Optional visual labels locate content by page and text, never chunk IDs."""
    rows = []
    for label in labels:
        left_page, right_page = label["leftPage"], label["rightPage"]
        left, right = norm(label["leftTail"]), norm(label["rightHead"])
        located_left = located_right = predicted = False
        for event in events:
            for block in event.get("blocks", []):
                if (block.get("label") == "table") != (label.get("kind") == "table"):
                    continue
                parts = block.get("source_fragments") or [block]
                has_left = any(left_page in part.get("pages", []) and left in norm(part.get("text", "")) for part in parts)
                has_right = any(right_page in part.get("pages", []) and right in norm(part.get("text", "")) for part in parts)
                located_left |= has_left
                located_right |= has_right
                predicted |= has_left and has_right
        verified = label.get("basis") == "VISUALLY_CHECKED"
        testable = bool(left and right and located_left and located_right and verified)
        rows.append({**label, "leftLocated": located_left, "rightLocated": located_right,
                     "testable": testable, "predictedMerged": predicted,
                     "correct": predicted == label["shouldMerge"] if testable else None})
    scored = [row for row in rows if row["testable"]]
    tp = sum(row["shouldMerge"] and row["predictedMerged"] for row in scored)
    fp = sum(not row["shouldMerge"] and row["predictedMerged"] for row in scored)
    fn = sum(row["shouldMerge"] and not row["predictedMerged"] for row in scored)
    tn = sum(not row["shouldMerge"] and not row["predictedMerged"] for row in scored)
    return {"labelsDenominator": len(rows), "testableLabels": len(scored), "excludedLabels": len(rows)-len(scored),
            "truePositive": tp, "falsePositive": fp, "falseNegative": fn, "trueNegative": tn,
            "precisionNumerator": tp, "precisionDenominator": tp + fp,
            "recallNumerator": tp, "recallDenominator": tp + fn, "boundaries": rows}


def window_diagnostics(folder, visual_gold):
    """Keep partial-page diagnostics separate from complete-document A/B runs."""
    diagnostics = []
    for result_path in sorted(folder.glob("*/window-result.json")):
        directory = result_path.parent
        control_path, new_path = directory / "window-control.json", directory / "window-new.json"
        if not control_path.exists() or not new_path.exists():
            continue
        result = load(result_path)
        control, new = [load(control_path)], [load(new_path)]
        key = result["documentId"]
        labels = [label for label in visual_gold if label.get("documentId") == key]
        table_details = []
        for label in labels:
            if label.get("kind") != "table":
                continue
            for side, page, anchor in (("left", label["leftPage"], label["leftTail"]),
                                       ("right", label["rightPage"], label["rightHead"])):
                candidates = [table for table in tables_from(control, page)
                              if any(norm(anchor) in norm(cell.get("text", ""))
                                     for cell in table["data"].get("table_cells", []))]
                if len(candidates) != 1:
                    table_details.append({"side": side, "page": page, "candidateCount": len(candidates),
                                          "status": "TABLE_ANCHOR_NOT_UNIQUELY_LOCATED"})
                    continue
                cells = candidates[0]["data"].get("table_cells", [])
                numbered = [cell for cell in cells if cell.get("start_col_offset_idx") == 0
                            and cell.get("end_col_offset_idx") == 1
                            and re.fullmatch(r"\d+", cell.get("text", "").strip())]
                numbered.sort(key=lambda cell: cell["start_row_offset_idx"])
                first_numbered = numbered[0] if numbered else None
                first_row_cells = [cell for cell in cells if first_numbered and
                                   cell.get("start_row_offset_idx") == first_numbered["start_row_offset_idx"]]
                table_details.append({"side": side, "page": page, "status": "LOCATED", "candidateCount": 1,
                                      "numberedRows": [cell["text"].strip() for cell in numbered],
                                      "columnHeaderTexts": [cell.get("text", "") for cell in cells if cell.get("column_header")],
                                      "firstNumberedRowHeaderCells": sum(bool(cell.get("column_header")) for cell in first_row_cells),
                                      "firstNumberedRowCellCount": len(first_row_cells)})
        diagnostics.append({"documentId": key, "scope": "PHYSICAL_PAGE_WINDOW_NOT_FULL_DOCUMENT",
                            "fullDocumentParsed": False, "selectedPhysicalPages": result["selectedPhysicalPages"],
                            "originalDocumentPages": result["originalDocumentPages"], "run": result,
                            "inputSha256": {path.name: digest(path) for path in (result_path, control_path, new_path)},
                            "sameConversionConservation": source_conservation(control, new),
                            "visualBoundaryGold": {"control": boundary_gold_score(control, labels),
                                                   "new": boundary_gold_score(new, labels)},
                            "tableDetails": table_details,
                            "newContinuationGroups": continuation_groups(new),
                            "excludedFromFullDocumentTimingAndCounts": True})
    return diagnostics


def score_variant(events, cases, pages, table_gold):
    records = source_records(events)
    groups = continuation_groups(events)
    return {"completeEventSeen": any(event.get("type") == "complete" for event in events),
            "errors": [event for event in events if event.get("type") == "error"],
            "batchCount": sum(event.get("type") == "batch" for event in events),
            "blockCount": sum(len(event.get("blocks", [])) for event in events),
            "originalFragmentCount": len(records),
            "pagesWithText": len({page for record in records if record.get("text", "").strip() for page in record.get("pages", [])}),
            "pagesDenominator": pages, "necessaryText": atoms_score(records, cases),
            "sourceGeometry": geometry(records, pages), "continuationGroups": groups,
            "continuationGroupCount": len(groups),
            "crossBatchContinuationGroups": sum(group["crossBatch"] for group in groups),
            "tableGold": table_relation_score(events, table_gold) if table_gold else None}


def ratio(numerator, denominator):
    return f"{numerator}/{denominator}"


def write_report(results, folder):
    lines = ["# 同文档重解析质量对比", "",
             "本报告分别检查抽样必要原文、表格行列数值对应、来源坐标和实际续接边界。旧解析只是历史参考，"
             "原文未丢失不等于解析准确、续接正确或问答准确。control/new 为同环境同配置、仅续接开关不同的对照；"
             "历史解析耗时含索引，不与本轮解析链路时长比较。", ""]
    for document in results["documents"]:
        variants = document["variants"]
        new = variants["new"]
        if "control" in variants:
            control = variants["control"]
            ca, na = control["necessaryText"], new["necessaryText"]
            sentence = (f"{document['language']}：必要原文完整题 control {ca['completeCases']}/{ca['casesDenominator']}，"
                        f"new {na['completeCases']}/{na['casesDenominator']}；新增续接组 {new['continuationGroupCount']}，"
                        f"其中跨批次 {new['crossBatchContinuationGroups']}。")
            if new["tableGold"] and control["tableGold"]:
                sentence += (f" 表格数值对应 control {control['tableGold']['correctRelations']}/{control['tableGold']['relationsDenominator']}，"
                             f"new {new['tableGold']['correctRelations']}/{new['tableGold']['relationsDenominator']}。")
            lines.extend([sentence, ""])
    lines.extend([
             "| 文档/版本 | 有文本页 | 必要原文完整题 | 必要原文片段 | 坐标可用来源 | 续接组/跨批组 | 表格对应数值 |",
             "|---|---:|---:|---:|---:|---:|---:|"])
    for document in results["documents"]:
        for name, item in document["variants"].items():
            atoms, geom, table = item["necessaryText"], item["sourceGeometry"], item["tableGold"]
            lines.append(f"| {document['language']}/{name} | {ratio(item['pagesWithText'],item['pagesDenominator'])} | "
                         f"{ratio(atoms['completeCases'],atoms['casesDenominator'])} | "
                         f"{ratio(atoms['foundAtoms'],atoms['atomsDenominator'])} | "
                         f"{ratio(geom['sourcesWithAtLeastOneValidBox'],geom['sourcesDenominator'])} | "
                         f"{item['continuationGroupCount']}/{item['crossBatchContinuationGroups']} | "
                         f"{ratio(table['correctRelations'],table['relationsDenominator']) if table else '未标注，不计分'} |")
    lines.extend(["", "必要原文来自既有 6 道题、18 个文本片段，对应原页由 Codex 本轮目视复核；属于小规模保留检查，"
                  "并非独立人工标注或未参与调试的留出题集；"
                  "表格为英文论文物理第 5 页已视觉核对的 Table 2，6 行×6 列共 36 项数值关系，缺表、缺行、列错位和"
                  "不明确的跨行/跨列数值都不会按命中计分。中文文件没有表格标注，不虚设分母。", "",
                  "| 文档/本次运行 | 首个批次（秒） | 首批非空解析结果（秒） | 解析链路总时长（秒） | 状态 |",
                  "|---|---:|---:|---:|---|"])
    for document in results["documents"]:
        for variant in ("control", "new"):
            timing = document.get("timings", {}).get(variant)
            if timing:
                record = timing["record"]
                def seconds(key):
                    value = record.get(key)
                    return f"{value:.3f}" if isinstance(value, (int, float)) else "未记录"
                lines.append(f"| {document['language']}/{variant} | {seconds('firstBatchSeconds')} | "
                             f"{seconds('firstVisibleBatchSeconds')} | {seconds('elapsedSeconds')} | "
                             f"{record.get('status','未记录')} |")
    lines.extend(["", "两轮均为单次运行，启动顺序在中英文之间交叉；缓存、预热和系统负载仍可能影响时间，"
                  "不能由一次差值声称统计显著提速。计时含解析子进程、续接组装及评测事件落盘，不含上传、索引或 LLM。"
                  "首批非空结果不等于首批向量索引可查询时间。", ""])
    for document in results["documents"]:
        lines.extend([f"## {document['language']} / {document['documentId']}", ""])
        for name, comparison in document["textComparisons"].items():
            lines.append(f"- {name}：{comparison['exactlyEqualPages']}/{comparison['pagesDenominator']} 页规范化原文完全相同；"
                         f"有序相同字符 {comparison['orderedMatchingCharacters']}，比较前/后字符分母分别为 "
                         f"{comparison['beforeCharactersDenominator']}/{comparison['afterCharactersDenominator']}。"
                         "这是输出差异，不能解释为原文准确率。")
        conservation = document.get("sameConversionConservation")
        if conservation:
            lines.append(f"- 同一次转换续接前后：原始来源 {conservation['originalSourcesDenominator']}，"
                         f"逐字段保留 {conservation['preservedExactOriginalSources']}，缺失或变化 "
                         f"{conservation['missingOrChangedSources']}，额外或重复 {conservation['extraOrDuplicatedSources']}。")
        else:
            lines.append("- 尚无同次转换 new-raw-events.jsonl，无法隔离判断续接自身是否丢失或改变原始来源。")
        ablation = document.get("sameInputAssemblyTiming")
        if ablation:
            disabled, enabled = ablation["modes"]["disabled"], ablation["modes"]["enabled"]
            lines.append(f"- 同一份已解析输入做续接关/开 {ablation['repetitions']} 次交叉回放：仅组装平均耗时 "
                         f"{disabled['meanMilliseconds']:.3f}/{enabled['meanMilliseconds']:.3f} 毫秒；"
                         "已排除首轮模块预热；这隔离了模型推理和冷启动，不等于完整解析性能。")
        ct, nt = document.get("timings", {}).get("control"), document.get("timings", {}).get("new")
        if ct and nt:
            control_total, new_total = ct["record"].get("elapsedSeconds"), nt["record"].get("elapsedSeconds")
            if isinstance(control_total, (int, float)) and isinstance(new_total, (int, float)) and control_total > 0:
                delta = new_total - control_total
                lines.append(f"- 本次 new 总时长相对 control 差值 {delta:+.3f} 秒（{delta/control_total:+.1%}）；"
                             "仅描述本次测量，不据此归因于算法或宣称稳定收益。")
        for variant, score in document.get("visualBoundaryGold", {}).items():
            lines.append(f"- {variant} 原页目视核对边界：可定位并已核对 {score['testableLabels']}/{score['labelsDenominator']}，"
                         f"TP/FP/FN/TN={score['truePositive']}/{score['falsePositive']}/{score['falseNegative']}/{score['trueNegative']}；"
                         f"合并精确率分子/分母={score['precisionNumerator']}/{score['precisionDenominator']}，"
                         f"召回分子/分母={score['recallNumerator']}/{score['recallDenominator']}。分母为0时不定义百分比。")
        for name, item in document["variants"].items():
            if item["errors"] or not item["completeEventSeen"]:
                lines.append(f"- **{name} 没有完整成功结束**：错误数 {len(item['errors'])}；不能将部分输出视为完整解析结果。")
        groups = document["variants"]["new"]["continuationGroups"]
        lines.extend(["", "实际触发的续接边界（匹配已核对标签者明确标示，其余仍待视觉核对）：", ""])
        if not groups:
            lines.append("没有触发续接；不能由此声称跨页段落或表格质量得到改善。")
        for group in groups:
            reviewed = "已匹配 Codex 目视正例" if group.get("visualReviewIds") else "待视觉核对"
            lines.append(f"- 物理页 {group['pages']}，{group['kind']}，跨批次={group['crossBatch']}；{group['reason']}；{reviewed}。")
            for index, source in enumerate(group["sources"]):
                snippet = source.get("text", "")[-180:] if index == 0 else source.get("text", "")[:180]
                lines.append("  - `" + snippet.replace("`", "'").replace("\n", " ") + "`")
        lines.append("")
    for diagnostic in results.get("windowDiagnostics", []):
        run = diagnostic["run"]
        pages = diagnostic["selectedPhysicalPages"]
        conservation = diagnostic["sameConversionConservation"]
        lines.extend(["## 华为英文可持续发展报告局部页段诊断", "",
                      f"源文件 {run['sourceBytes']:,} 字节、共 {diagnostic['originalDocumentPages']} 页；"
                      f"本轮只实际解析物理页 {pages}，窗口耗时 {run['pageWindowElapsedSeconds']:.3f} 秒。"
                      "这是一次实际转换后的续接关/开比较，没有为两种组装方式重复模型推理；"
                      "不算第三份全文解析，也不加入前述全篇耗时比较。", "",
                      f"- **来源保留**：{conservation['preservedExactOriginalSources']}/{conservation['originalSourcesDenominator']} "
                      f"原始内容块逐字段保留；缺失或变化 {conservation['missingOrChangedSources']}，"
                      f"额外或重复 {conservation['extraOrDuplicatedSources']}。这不代表表头语义正确。"])
        for table in diagnostic["tableDetails"]:
            if table["status"] == "LOCATED":
                lines.append(f"- 物理第 {table['page']} 页表格行号：{', '.join(table['numberedRows'])}；"
                             f"首个编号数据行有 {table['firstNumberedRowHeaderCells']}/{table['firstNumberedRowCellCount']} "
                             "个单元格被解析器标为列头。")
        if run.get("page35FirstDataRowMisclassifiedAsHeader"):
            lines.append("- **表头语义错误**：原页目视可确认第 35 页第 14 条是数据行，Docling 却把该行 5 个单元格标为列头；"
                         "因此与第 34 页真实表头不匹配。行号和正文还在，不能把它描述成原文丢失。")
        for name, score in diagnostic["visualBoundaryGold"].items():
            lines.append(f"- **{name} 续表结果**：可定位的目视正例 {score['testableLabels']}/{score['labelsDenominator']}，"
                         f"实际正确合并 {score['truePositive']}，漏合并 {score['falseNegative']}，错误合并 {score['falsePositive']}。")
        lines.extend(["- **结论**：这一页段证明当前规则仍会漏掉没有重复真表头、且首行被误标表头的真实续表。"
                      "本轮保留该失败，不通过放宽规则掩盖问题；来源保留、表头分类、续表关系是三个分别评估的维度。", ""])
    lines.extend(["## 指标边界", "",
                  "- 坐标合法只检查页码、坐标原点、范围和数值，不代表框选位置与原文视觉完全一致。",
                  "- 多页来源无法可靠拆到单页时从逐页字符比较排除，在 JSON 单独列出，避免一份原文重复计入多个页面。",
                  "- 续接组数不是准确率。只有对应该合并/不该合并的原页目视核对边界标签，才能计算合并 precision/recall。",
                  "- 本轮不调用 Embedding、Reranker 或聊天 LLM；没有评估最终召回或回答质量。",
                  "- 若增加华为英文可持续发展报告局部页段，必须单列页段分母，不能计作完整 56 页文档解析完成。", ""])
    (folder / "quality-report.md").write_text("\n".join(lines), encoding="utf-8")


def self_test():
    gold = next(table for table in load(ROOT / "evaluation/parser-ab-20261018/table-gold.json")["tables"]
                if table["document"] == "perf-5a023e1dce750ab2")
    baseline = events_at(ROOT / "evaluation/chunk-expanded-20261019/inputs/perf-5a023e1dce750ab2/events.jsonl")
    score = table_relation_score(baseline, gold)
    assert score["correctRelations"] == 36, score
    wrong = deepcopy(baseline)
    for event in wrong:
        for table in event.get("doclingDocument", {}).get("tables", []):
            if any(p.get("page_no") == 5 for p in table.get("prov", [])):
                for cell in table.get("data", {}).get("table_cells", []):
                    if not cell.get("column_header") and re.fullmatch(r"\d+\.\d+", cell.get("text", "")):
                        cell["text"] = str(Decimal(cell["text"]) + 1)
    assert table_relation_score(wrong, gold)["correctRelations"] == 0
    swapped = deepcopy(baseline)
    for event in swapped:
        for table in event.get("doclingDocument", {}).get("tables", []):
            if any(p.get("page_no") == 5 for p in table.get("prov", [])):
                for cell in table.get("data", {}).get("table_cells", []):
                    col = cell.get("start_col_offset_idx")
                    if not cell.get("column_header") and col in (1, 2) and cell.get("end_col_offset_idx") == col + 1:
                        cell["start_col_offset_idx"] = 3 - col
                        cell["end_col_offset_idx"] = 4 - col
    assert table_relation_score(swapped, gold)["correctRelations"] == 24
    print("Scorer checks passed: visual table gold 36/36; changed numbers 0/36; swapped two numeric columns 24/36.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--self-test", action="store_true")
    options = parser.parse_args()
    if options.self_test:
        self_test()
        return
    folder = options.directory.resolve()
    if folder != DEFAULT_OUTPUT.resolve() and DEFAULT_OUTPUT.resolve() not in folder.parents:
        raise ValueError("Report directory must stay within evaluation/boundary-quality-20261026")
    manifest_path = ROOT / "evaluation/chunk-expanded-20261019/manifest.json"
    manifest = load(manifest_path)
    table_gold_path = ROOT / "evaluation/parser-ab-20261018/table-gold.json"
    gold = load(table_gold_path)["tables"]
    visual_path = folder / "boundary-gold.json"
    visual_gold = load(visual_path).get("boundaries", []) if visual_path.exists() else []
    results = {"method": "SAME_DOCUMENT_CONTROL_AND_CONTINUITY_PARSE_COMPARISON", "llmCalls": 0,
               "inferenceCallsByScorer": 0, "documents": [],
               "scorerSha256": digest(Path(__file__).resolve()),
               "manifestSha256": digest(manifest_path), "tableGoldSha256": digest(table_gold_path),
               "limitations": ["Frozen old output is not ground truth.", "Necessary-text source pages were visually checked by Codex, not an independent human-heldout benchmark.",
                               "Only selected visually checked table relationships are scored; no full TEDS or answer accuracy.",
                               "Geometry validity does not establish visually correct coordinates.",
                               "Unreviewed merge counts do not establish semantic merge precision or recall."]}
    for key in DOC_KEYS:
        doc = next(document for document in manifest["documents"] if document["id"] == key)
        cases = [case for case in doc["cases"] if case.get("variant", "ORIGINAL") == "ORIGINAL"]
        paths = {"history": ROOT / "evaluation/chunk-expanded-20261019/inputs" / key / "events.jsonl",
                 "control": folder / key / "control-events.jsonl", "new": folder / key / "new-events.jsonl"}
        if not paths["new"].exists():
            raise FileNotFoundError(f"New parse not yet available: {paths['new']}; scorer never launches parsing")
        events = {name: events_at(path) for name, path in paths.items() if path.exists()}
        variants = {name: score_variant(value, cases, doc["pages"], next((g for g in gold if g["document"] == key), None))
                    for name, value in events.items()}
        comparisons = {f"history-vs-{name}": text_delta(source_records(events["history"]), source_records(events[name]), doc["pages"])
                       for name in ("control", "new") if name in events}
        if "control" in events:
            comparisons["control-vs-new"] = text_delta(source_records(events["control"]), source_records(events["new"]), doc["pages"])
        raw_path = folder / key / "new-raw-events.jsonl"
        item = {"documentId": key, "language": doc["language"], "pages": doc["pages"], "variants": variants,
                "inputSha256": {name: digest(path) for name, path in paths.items() if path.exists()},
                "textComparisons": comparisons,
                "timings": {name: run_timing(folder / key, name) for name in ("control", "new")}}
        labels = [label for label in visual_gold if label.get("documentId") == key]
        if labels:
            item["visualBoundaryGold"] = {name: boundary_gold_score(value, labels) for name, value in events.items()}
            for group in variants["new"]["continuationGroups"]:
                review_ids = []
                for label in labels:
                    if not label.get("shouldMerge") or label.get("basis") != "VISUALLY_CHECKED" or label.get("kind") != group["kind"]:
                        continue
                    left_found = any(label["leftPage"] in source.get("pages", []) and
                                     norm(label["leftTail"]) in norm(source.get("text", "")) for source in group["sources"])
                    right_found = any(label["rightPage"] in source.get("pages", []) and
                                      norm(label["rightHead"]) in norm(source.get("text", "")) for source in group["sources"])
                    if left_found and right_found:
                        review_ids.append(label.get("id"))
                if review_ids:
                    group.update(visualReviewIds=review_ids, semanticCorrectness="CODEX_VISUALLY_CHECKED_SMALL_PURPOSIVE_SAMPLE")
        if raw_path.exists():
            raw_events = events_at(raw_path)
            item["sameConversionConservation"] = source_conservation(raw_events, events["new"])
            item["sameInputAssemblyTiming"] = assembly_ablation(raw_events, (item["timings"].get("new") or {}).get("record", {}).get("settings"))
            item["inputSha256"]["newRaw"] = digest(raw_path)
        results["documents"].append(item)
    results["windowDiagnostics"] = window_diagnostics(folder, visual_gold)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "quality-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(results, folder)
    print(json.dumps({"documents": len(results["documents"]), "llmCalls": 0,
                      "reports": [str(folder / "quality-results.json"), str(folder / "quality-report.md")]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
