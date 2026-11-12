import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from prescan import sample_pages,prescan_document


class PrescanTests(unittest.TestCase):
    def test_sampling_is_deterministic_bounded_and_covers_ends(self):
        for total in (0,1,8,17,56,148,1000):
            pages=sample_pages(total,12)
            self.assertEqual(pages,sorted(set(pages)))
            self.assertLessEqual(len(pages),12)
            if total:self.assertEqual(1,pages[0]);self.assertEqual(total,pages[-1])
        with self.assertRaises(ValueError):sample_pages(10,0)

    def test_budget_and_size_fail_before_loading_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"x.pdf";path.write_bytes(b"123")
            for settings in ({"max_bytes":2},{"max_bytes":100,"prescan_max_pages":0}):
                with self.assertRaises(ValueError):prescan_document(str(path),settings)

    def test_docx_plan_does_not_switch_checkpoint_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"x.docx";path.write_bytes(b"123")
            with patch("adaptive_pipeline.profile_docx",return_value={"parser":"DOCX_STREAM","format":"docx"}):
                result=prescan_document(str(path),{"max_bytes":100})
            self.assertEqual("DOCLING",result["plan"]["parser"])
            self.assertEqual("DOCX_STREAM",result["plan"]["nativeCandidate"])


if __name__=="__main__":unittest.main()
