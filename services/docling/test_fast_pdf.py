import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fast_pdf import convert_fast_pdf


class Resource:
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def __len__(self): return 3
    def __getitem__(self, index): return Page(index)


class Page(Resource):
    def __init__(self, index): self.index = index
    def get_textpage(self): return self
    def get_text_bounded(self): return ["第一章 基础\n第一页面", "第二页面：Redis 缓存", ""][self.index]


class Output:
    def __init__(self): self.events = []
    def put(self, event): self.events.append(event)


class FastPdfTests(unittest.TestCase):
    def settings(self, **kwargs):
        return dict(max_pages=1000, resume_pages=0, max_chars=100000, fast_batch_pages=2, **kwargs)

    def run_conversion(self, settings):
        output = Output()
        with patch.dict(sys.modules, {"pypdfium2": SimpleNamespace(PdfDocument=lambda _: Resource(), PdfiumError=RuntimeError)}):
            convert_fast_pdf("test.pdf", settings, output)
        return output.events

    def test_text_pages_heading_and_blank_page_warning(self):
        events = self.run_conversion(self.settings())
        batches = [e for e in events if e["type"] == "batch"]
        self.assertEqual([2, 3], [e["endPage"] for e in batches])
        self.assertEqual([2], batches[0]["blocks"][1]["pages"])
        self.assertIn("Redis", batches[0]["blocks"][1]["text"])
        self.assertEqual("第一章 基础", batches[0]["blocks"][1]["section"])
        self.assertEqual([], batches[1]["blocks"])
        self.assertTrue(any("3" in warning and "OCR" in warning for warning in batches[1]["warnings"]))
        self.assertEqual("complete", events[-1]["type"])

    def test_resume_skips_previously_completed_pages(self):
        settings = self.settings(); settings["resume_pages"] = 1
        events = self.run_conversion(settings)
        batch = events[1]
        self.assertEqual(2, batch["startPage"])
        self.assertEqual([2], batch["blocks"][0]["pages"])
        self.assertFalse(any("第一页面" in b["text"] for b in batch["blocks"]))

    def test_capacity_and_checkpoint_limits(self):
        settings = self.settings(); settings["max_chars"] = 1
        with self.assertRaises(ValueError): self.run_conversion(settings)
        settings = self.settings(); settings["resume_pages"] = 4
        with self.assertRaises(ValueError): self.run_conversion(settings)


if __name__ == "__main__": unittest.main()
