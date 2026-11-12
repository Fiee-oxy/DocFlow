import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("docx_runner", Path(__file__).with_name("run-docx-stream-ab.py"))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class DocxRunnerTests(unittest.TestCase):
    def test_complete_requires_explicit_terminal_event_and_keeps_events_unmodified(self):
        events = [{"type": "metadata"}, {"type": "batch", "blocks": [{"text": "A"}], "resumeState": {"nextBatch": 1}}, {"type": "complete"}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            measured, states = r.collect("source.docx", r.base_settings(), path, lambda *_: iter(events))
            self.assertEqual("COMPLETE", measured["status"])
            self.assertEqual(events, [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()])
            self.assertIsNotNone(measured["first_batch_ms"])
            self.assertIsNotNone(measured["total_ms"])
            self.assertEqual([{"resumeState": {"nextBatch": 1}}], states)
            self.assertNotIn("blocks", states[0])

    def test_nonterminal_stream_and_exception_never_become_success(self):
        def broken(*_):
            yield {"type": "metadata"}
            raise RuntimeError("conversion broke")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            incomplete, _ = r.collect("source", {}, path, lambda *_: iter([{"type": "metadata"}]))
            failed, _ = r.collect("source", {}, path, broken)
            self.assertEqual("INCOMPLETE_STREAM", incomplete["status"])
            self.assertEqual("EXCEPTION", failed["status"])
            self.assertEqual("RuntimeError", failed["exception_type"])

    def test_strict_unsupported_remains_rejected_without_fallback(self):
        events = [{"type": "error", "message": "DOCX_UNSUPPORTED_IMAGE: normal parser required", "retryable": False}]
        calls = []
        def parser(source, settings):
            calls.append(settings["parse_mode"])
            yield from events
        with tempfile.TemporaryDirectory() as directory:
            measured, _ = r.collect("source", {"parse_mode": "DOCX_STREAM"}, Path(directory) / "events.jsonl", parser)
            self.assertEqual("REJECTED_UNSUPPORTED", measured["status"])
            self.assertEqual(["DOCX_STREAM"], calls)
            self.assertIsNone(measured["first_batch_ms"])

    def test_first_middle_end_checkpoint_counts_are_deduplicated(self):
        self.assertEqual([], r.checkpoint_counts(0))
        self.assertEqual([{"afterBatch": 1, "positions": ["first", "middle", "end"]}], r.checkpoint_counts(1))
        self.assertEqual([1, 5, 10], [c["afterBatch"] for c in r.checkpoint_counts(10)])

    def test_diagnostic_partial_terminal_never_becomes_complete_success(self):
        events = [{"type": "batch", "blocks": [{"text": "Body"}], "partial": True,
                   "warnings": ["DOCX_UNSUPPORTED_IMAGE: image omitted"]}, {"type": "complete"}]
        with tempfile.TemporaryDirectory() as directory:
            measured, _ = r.collect("source", {"docx_strict": False}, Path(directory) / "diagnostic.jsonl", lambda *_: iter(events))
            self.assertEqual("PARTIAL_UNSUPPORTED", measured["status"])

    def test_resume_passes_serialized_source_cursor_and_persists_suffix(self):
        batches = [{"resumeState": {"nextBodyNode": i, "nextBatch": i, "heading": ["Title"]}} for i in range(1, 5)]
        calls, saved = [], []
        def parser(source, settings):
            calls.append(settings)
            yield {"type": "metadata", "resumedFromNode": settings["resume_state"]["nextBodyNode"]}
            yield {"type": "complete"}
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            checks = r.resume_runs("source.docx", r.base_settings(), folder, batches, parser, lambda value: saved.append(list(value)))
            self.assertEqual([1, 2, 4], [c["afterBatch"] for c in checks])
            self.assertEqual([1, 2, 4], [c["resume_batches"] for c in calls])
            self.assertTrue(all(c["resume_pages"] == 0 and c["heading"] == "Title" for c in calls))
            self.assertTrue(all(c["status"] == "COMPLETE" and not c["includesPrefix"] for c in checks))
            self.assertEqual(3, len(saved))
            for check in checks:
                self.assertTrue((folder / check["eventsPath"]).is_file())


if __name__ == "__main__":
    unittest.main()
