"""Bounded tail carry: committed parser batches are immutable and resume is lossless."""
import copy
import json
from continuity import merge_boundary, source_fragments, can_defer

VERSION = 1
RULES_VERSION = "continuity-v1"
MAX_STATE_BYTES = 240 * 1024  # Leave headroom under Java's 256 KiB wire limit.
MAX_CARRY_CHARS = 20000


class BoundaryAssembler:
    def __init__(self, settings):
        self.settings = settings
        self.pending = None
        self.batch_index = settings.get("resume_batches", 0)
        self.contract = {
            "mode": settings.get("parse_mode", "DOCLING"),
            "batchPages": settings.get("fast_batch_pages", 32) if settings.get("parse_mode") == "FAST" else settings.get("batch_pages", 4),
            "ocr": settings.get("ocr_enabled", False),
            "ocrLanguages": settings.get("ocr_languages", ["chi_sim", "eng"]),
            "tableMode": settings.get("table_mode", "fast"),
            "rules": RULES_VERSION,
        }
        if settings.get("parse_mode") in {"AUTO", "HIGH_ACCURACY"}:
            self.contract["adaptiveRevision"] = "adaptive-parser-v1"
            self.contract["batchPages"] = 1
        state = settings.get("resume_state")
        # Old in-progress parses retain their original semantics; do not invent a missing tail.
        self.enabled = state is not None or (settings.get("continuity_enabled", True) and
            settings.get("resume_pages", 0) == 0 and self.batch_index == 0)
        settings["_continuity_active"] = self.enabled
        if state is not None:
            if not isinstance(state, dict) or state.get("version") != VERSION or state.get("contract") != self.contract:
                raise ValueError("跨页续接检查点版本或解析配置已改变，请恢复原配置或重新解析")
            if state.get("parsedThroughPage") != settings.get("resume_pages", 0):
                raise ValueError("跨页续接检查点与已解析页数不一致")
            if len(json.dumps(state, ensure_ascii=False).encode("utf-8")) > MAX_STATE_BYTES:
                raise ValueError("跨页续接检查点超过容量限制")
            self.pending = copy.deepcopy(state.get("pending"))
            if self.pending is not None:
                if not isinstance(self.pending, dict) or len(self.pending.get("text", "")) > MAX_CARRY_CHARS:
                    raise ValueError("待续内容检查点不合法")
                pages = self.pending.get("pages", [])
                if not pages or max(pages) > settings.get("resume_pages", 0):
                    raise ValueError("待续内容页码超过解析检查点")
            heading = state.get("heading", [])
            if not isinstance(heading, list) or any(not isinstance(part, str) for part in heading):
                raise ValueError("待续章节检查点不合法")
            settings["heading"] = " / ".join(heading)

    @staticmethod
    def _can_defer(block):
        return can_defer(block,max_chars=MAX_CARRY_CHARS)

    def process(self, event):
        if not self.enabled:
            return event
        if event["type"] == "complete":
            if self.pending:
                raise ValueError("解析流结束时仍有未提交的跨页待续内容")
            return event
        if event["type"] != "batch":
            return event
        result = copy.deepcopy(event)
        blocks = result.get("blocks", [])
        for block in blocks:
            block.setdefault("batch_index", self.batch_index)
            block["source_fragments"] = source_fragments(block)
        had_pending = self.pending is not None
        if self.pending:
            blocks.insert(0, self.pending)
            self.pending = None
        joined = []
        merges = 0
        for block in blocks:
            merged = merge_boundary(joined[-1], block) if joined else None
            if merged is not None:
                joined[-1] = merged
                merges += 1
            else:
                joined.append(block)
        final = result.get("endPage", 0) == result.get("totalPages", 0)
        state = {"version": VERSION, "contract": self.contract, "pending": None,
                 "parsedThroughPage": result.get("endPage", 0), "heading": result.get("headingState", [])}
        limited = False
        if not final and joined and max(joined[-1].get("pages") or [0]) == result.get("endPage") and self._can_defer(joined[-1]):
            candidate = joined[-1]
            proposed = dict(state, pending=candidate)
            if len(json.dumps(proposed, ensure_ascii=False).encode("utf-8")) <= MAX_STATE_BYTES:
                self.pending = joined.pop()
                state = proposed
            else:
                limited = True
        result["blocks"] = joined
        result["resumeState"] = state
        result["continuity"] = {"version": RULES_VERSION, "mergedBoundaries": merges,
                                "deferred": self.pending is not None, "carryLimitReached": limited,
                                "requiresCanonical": had_pending or merges > 0 or self.pending is not None}
        warnings = result.setdefault("warnings", [])
        warnings[:] = [w for w in warnings if "跨批次的表格尚未自动合并" not in w]
        warnings.append("已启用保守跨页续接；仅合并位置、章节与正文或表头证据一致的片段，不确定的边界保留原样。")
        if limited:
            warnings.append("页尾待续内容超过检查点容量，已原样提交，未跨批强行拼接。")
        if "doclingDocument" in result and result["continuity"]["requiresCanonical"]:
            from chunking_document import attach_chunking_document
            attach_chunking_document(result)
        self.batch_index += 1
        return result
