import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from adaptive_pipeline import Capture, convert_adaptive, profile_docx, quality, route_page


class AdaptiveTests(unittest.TestCase):
    def test_router_is_explainable_and_local(self):
        simple = dict(page=1, characters=300, garblingRatio=0, layoutAmbiguous=False)
        self.assertEqual("FAST", route_page(simple)[0])
        for change in [dict(characters=0), dict(garblingRatio=.1), dict(tableSuspected=True),
                       dict(layoutAmbiguous=True), dict(imageCount=1)]:
            self.assertEqual("DOCLING", route_page(simple | change)[0])
        self.assertEqual("DOCLING", route_page(simple, "HIGH_ACCURACY")[0])

    def test_quality_does_not_invent_confidence(self):
        result = quality({"blocks": [{"text": "abc"}]}, {"characters": 200})
        self.assertIn("NATIVE_TEXT_LOSS", result["reasons"])
        self.assertIsNone(result["calibratedAccuracy"])
        self.assertIn("GARBLED_TEXT", quality({"blocks": [{"text": "\ufffd"*30}]})["reasons"])
        self.assertEqual("REVIEW_REQUIRED", quality({"blocks": []})["status"])

    def test_docx_complex_object_preflight_and_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"a.docx"
            for body, parser in [("<p><r><t>hello</t></r></p>", "DOCX_STREAM"), ("<drawing/>", "DOCLING")]:
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("word/document.xml", f"<document><body>{body}</body></document>")
                self.assertEqual(parser, profile_docx(path, {})["parser"])
            with self.assertRaises(ValueError):
                profile_docx(path, {"docx_max_xml_bytes": 1})

    def run_pdf(self, texts, resume=0, max_chars=10000):
        settings = dict(parse_mode="AUTO", max_pages=100, resume_pages=resume, max_chars=max_chars)
        profile = {"pages": [dict(page=i+1, characters=100, garblingRatio=0) for i in range(len(texts))]}
        calls = []
        def extract(source, settings, parser, start, end, **kwargs):
            calls.append((start, parser))
            text = texts[start-1] if parser == "FAST" else "recovered "*20
            return [{"type": "batch", "blocks": [{"text": text, "pages": [start]}], "headingState": [], "warnings": []}]
        output = Capture()
        with tempfile.TemporaryDirectory() as folder, patch("adaptive_pipeline.profile_pdf", return_value=profile), patch("adaptive_pipeline.extract", side_effect=extract):
            path = Path(folder)/"test.pdf"; path.write_bytes(b"fixture")
            convert_adaptive(str(path), settings, output)
        return output.events, calls

    def test_only_failed_page_is_upgraded_and_order_is_preserved(self):
        events, calls = self.run_pdf(["a"*100, "x", "b"*100])
        self.assertEqual([(1,"FAST"),(2,"FAST"),(2,"DOCLING"),(3,"FAST")], calls)
        self.assertEqual([1,2,3], [e["endPage"] for e in events if e["type"] == "batch"])
        self.assertEqual("complete", events[-1]["type"])

    def test_resume_and_capacity(self):
        _, calls = self.run_pdf(["a"*100, "b"*100], resume=1)
        self.assertEqual([(2,"FAST")], calls)
        with self.assertRaises(ValueError):
            self.run_pdf(["a"*100], max_chars=10)

    def test_fallback_still_bad_never_completes(self):
        output = Capture()
        with tempfile.TemporaryDirectory() as folder, patch("adaptive_pipeline.profile_pdf", return_value={"pages": [dict(page=1, characters=100, garblingRatio=0)]}), patch("adaptive_pipeline.extract", return_value=[{"type":"batch", "blocks": []}]):
            path = Path(folder)/"test.pdf"; path.write_bytes(b"fixture")
            convert_adaptive(str(path), dict(parse_mode="AUTO", max_pages=10, max_chars=1000), output)
        self.assertEqual("PARSER_QUALITY_REVIEW", output.events[-1]["failureType"])
        self.assertFalse(any(e["type"] == "complete" for e in output.events))

    def test_unscanned_pages_are_profiled_before_extraction(self):
        first=dict(page=1,characters=100,garblingRatio=0)
        second=dict(page=2,characters=100,garblingRatio=0,tableSuspected=True)
        output=Capture();calls=[]
        def extractor(source,settings,parser,start,end,**kwargs):
            calls.append((start,parser))
            return [{"type":"batch","blocks":[{"text":"a"*100,"pages":[start]}],"headingState":[]}]
        with tempfile.TemporaryDirectory() as folder, patch("prescan.prescan_document",return_value={"pages":[first],"totalPages":2}), patch("adaptive_pipeline.profile_pdf",return_value={"pages":[second]}), patch("adaptive_pipeline.extract",side_effect=extractor):
            path=Path(folder)/"test.pdf";path.write_bytes(b"fixture")
            convert_adaptive(str(path),dict(parse_mode="AUTO",prescan_enabled=True,resume_pages=0,max_chars=1000),output)
        self.assertEqual([(1,"FAST"),(2,"DOCLING")],calls)
        self.assertEqual("complete",output.events[-1]["type"])


if __name__ == "__main__":
    unittest.main()
