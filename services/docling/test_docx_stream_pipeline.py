"""Real subprocess integration for the opt-in native DOCX path; no model calls."""
import importlib
import importlib.util
import io
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest
import zipfile

from pipeline import task_events
from docx_stream import W
from test_docx_stream import paragraph, table, row, cell


class DocxStreamPipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.source = Path(self.directory.name) / "integration.docx"
        self.write("".join(paragraph(f"Evidence {index}") for index in range(5)) +
                   table([row([cell("Key"), cell("Value")])], columns=2))

    def tearDown(self):
        self.directory.cleanup()

    def write(self, body):
        xml = f'<w:document xmlns:w="{W}"><w:body>{body}<w:sectPr/></w:body></w:document>'
        with zipfile.ZipFile(self.source, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", xml)

    def settings(self, **overrides):
        return {"parse_mode": "DOCX_STREAM", "timeout": 30, "resume_pages": 0,
                "resume_batches": 0, "resume_state": None, "heading": "",
                "docx_batch_nodes": 2, "docx_batch_chars": 12000, "docx_strict": True,
                "max_bytes": 209715200, "max_chars": 10000000, **overrides}

    @staticmethod
    def batches(events):
        return [event for event in events if event["type"] == "batch"]

    def test_production_task_events_uses_live_spawn_worker_and_cleans_it(self):
        children_before = {process.pid for process in multiprocessing.active_children()}
        stream = task_events(self.source, self.settings())
        try:
            first = next(event for event in stream if event["type"] != "heartbeat")
            self.assertEqual(first["type"], "metadata")
            spawned = {process.pid for process in multiprocessing.active_children()} - children_before
            self.assertTrue(spawned, "Expected a real child process, not a mocked worker")
            events = [first, *stream]
        finally:
            stream.close()
        self.assertEqual(events[-1]["type"], "complete")
        self.assertEqual(events[-1]["totalBodyNodes"], 7)
        self.assertFalse(any(event["type"] == "error" for event in events))
        self.assertFalse(spawned.intersection(process.pid for process in multiprocessing.active_children()))
        self.assertEqual([batch["endNode"] for batch in self.batches(events)], [2, 4, 6, 7])

    def test_production_resume_roundtrip_matches_remaining_batches_and_completion(self):
        full = list(task_events(self.source, self.settings()))
        batches = self.batches(full)
        for index in (0, len(batches) - 1):
            state = json.loads(json.dumps(batches[index]["docxResumeState"]))
            resumed = list(task_events(self.source, self.settings(resume_state=state, resume_batches=state["nextBatch"])))
            self.assertFalse(any(event["type"] == "error" for event in resumed), resumed)
            self.assertEqual(self.batches(resumed), batches[index + 1:])
            self.assertEqual(resumed[-1], full[-1])

    def test_production_events_never_invent_word_pages_or_pdf_tail_state(self):
        events = list(task_events(self.source, self.settings()))
        for event in events:
            if event["type"] == "heartbeat":
                continue
            self.assertEqual(event["totalPages"], 0)
            self.assertEqual(event["parseUnit"], "DOCX_BODY_NODE")
            self.assertNotIn("continuity", event)
            for block in event.get("blocks", []):
                self.assertEqual(block["pages"], [])
                self.assertEqual(block["parse_unit"], "DOCX_BODY_NODE")
                self.assertEqual(block["source_fragments"][0]["pages"], [])
            if event["type"] == "batch":
                self.assertEqual((event["startPage"], event["endPage"]), (0, 0))
                self.assertEqual(event["resumeState"]["parser"], "DOCX_STREAM")
                self.assertNotIn("pending", event["resumeState"])

    def test_unsupported_later_node_yields_failure_after_visible_prefix_not_complete(self):
        self.write(paragraph("first evidence") + '<w:p><w:r><w:drawing/></w:r></w:p>')
        events = list(task_events(self.source, self.settings(docx_batch_nodes=1)))
        self.assertEqual(self.batches(events)[0]["blocks"][0]["text"], "first evidence")
        self.assertEqual(events[-1]["type"], "error")
        self.assertFalse(events[-1]["retryable"])
        self.assertIn("DOCX_UNSUPPORTED_DRAWING_WITHOUT_IMAGE_OR_TEXT", events[-1]["message"])
        self.assertFalse(any(event["type"] == "complete" for event in events))

    def test_invalid_checkpoint_is_returned_as_nonretryable_error(self):
        full = list(task_events(self.source, self.settings()))
        state = self.batches(full)[0]["resumeState"]
        events = list(task_events(self.source, self.settings(resume_state=state, docx_batch_chars=99)))
        self.assertEqual(events[-1]["type"], "error")
        self.assertFalse(events[-1]["retryable"])
        self.assertIn("DOCX_CHECKPOINT_INVALID", events[-1]["message"])
        self.assertFalse(self.batches(events))


HAS_APP_DEPS = all(importlib.util.find_spec(name) is not None for name in ("fastapi", "python_multipart"))


@unittest.skipUnless(HAS_APP_DEPS, "App validation requires FastAPI and python-multipart; parser subprocess tests still run")
class DocxStreamAppModeTests(unittest.TestCase):
    def setUp(self):
        self.adapter = importlib.import_module("app")
        from starlette.datastructures import UploadFile
        self.UploadFile = UploadFile

    def upload(self, filename, content=b"not read during mode validation"):
        return self.UploadFile(file=io.BytesIO(content), filename=filename)

    def test_docx_stream_rejects_pdf_and_fast_rejects_docx(self):
        for filename, mode in (("document.pdf", "DOCX_STREAM"), ("document.docx", "FAST"), ("document.docx", "UNKNOWN")):
            upload = self.upload(filename)
            with self.assertRaises(self.adapter.HTTPException) as caught:
                self.adapter.ParseJob(upload, parse_mode=mode)
            self.assertEqual(caught.exception.status_code, 422)
            self.assertTrue(upload.file.closed)

    def test_default_remains_docling_and_stream_mode_uses_isolated_gate(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{paragraph("text")}<w:sectPr/></w:body></w:document>')
        default = self.adapter.ParseJob(self.upload("sample.docx", output.getvalue()))
        try:
            self.assertEqual(default.settings["parse_mode"], "DOCLING")
            self.assertIs(default.gate, self.adapter.lock)
        finally:
            default.close()
        stream = self.adapter.ParseJob(self.upload("sample.docx", output.getvalue()), parse_mode="DOCX_STREAM")
        try:
            self.assertEqual(stream.settings["parse_mode"], "DOCX_STREAM")
            self.assertIs(stream.gate, self.adapter.docx_stream_lock)
        finally:
            stream.close()

    def test_malformed_resume_json_rejected_before_task_creation(self):
        upload = self.upload("sample.docx")
        with self.assertRaises(self.adapter.HTTPException) as caught:
            self.adapter.ParseJob(upload, parse_mode="DOCX_STREAM", resume_state="{")
        self.assertEqual(caught.exception.status_code, 422)
        self.assertTrue(upload.file.closed)


if __name__ == "__main__":
    unittest.main()
