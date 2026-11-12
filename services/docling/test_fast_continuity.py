"""Real PDFium integration fixtures for page-edge carry and lossless fallback.

These PDFs use only a built-in PDF font and handwritten objects/xref entries;
no Docling model, OCR, network service, or report-generation package is needed.
"""
import copy
from contextlib import closing
import json
import tempfile
import unittest
from pathlib import Path

import pypdfium2 as pdfium

from boundary_stream import BoundaryAssembler
from fast_pdf import convert_fast_pdf


TITLE = "Chapter 1 Programme requirements"
FIRST = "Students in this programme must complete all mandatory modules and"
SECOND = "earn at least one hundred and sixty credits before graduation."


def pdf_bytes(pages, rotations=None):
    """Each page is a list of (x, baseline_y, ASCII text) in PDF coordinates."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for number, lines in enumerate(pages):
        page_number = len(objects) + 1
        content_number = page_number + 1
        kids.append(f"{page_number} 0 R")
        rotation = (rotations or {}).get(number, 0)
        objects.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] "
                        f"/Rotate {rotation} /Resources << /Font << /F1 3 0 R >> >> "
                        f"/Contents {content_number} 0 R >>").encode("ascii"))
        commands = []
        for x, y, text in lines:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"BT /F1 12 Tf 1 0 0 1 {x} {y} Tm ({escaped}) Tj ET")
        stream = ("\n".join(commands) + "\n").encode("ascii")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"endstream")
    objects[1] = f"<< /Type /Pages /Count {len(kids)} /Kids [{' '.join(kids)}] >>".encode("ascii")
    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend((f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
                 f"startxref\n{xref}\n%%EOF\n").encode("ascii"))
    return bytes(data)


def settings(**overrides):
    result = dict(max_pages=1000, resume_pages=0, resume_batches=0, max_chars=100000,
                  fast_batch_pages=1, parse_mode="FAST", continuity_enabled=True,
                  ocr_enabled=False)
    result.update(overrides)
    return result


class AssembledOutput:
    def __init__(self, config):
        self.assembler = BoundaryAssembler(config)
        self.raw = []
        self.events = []

    def put(self, event):
        self.raw.append(copy.deepcopy(event))
        self.events.append(self.assembler.process(event))


class RealFastContinuityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.file = Path(self.directory.name) / "fixture.pdf"

    def convert(self, pages=None, config=None, rotations=None):
        if pages is not None:
            self.file.write_bytes(pdf_bytes(pages, rotations))
        config = settings() if config is None else config
        output = AssembledOutput(config)
        convert_fast_pdf(self.file, config, output)
        return output

    @staticmethod
    def batches(events):
        return [event for event in events if event["type"] == "batch"]

    def test_actual_pdfium_coordinates_merge_and_resume_are_identical(self):
        output = self.convert([[(40, 750, TITLE), (40, 55, FIRST)], [(40, 750, SECOND)]])
        raw = self.batches(output.raw)
        first_units = raw[0]["blocks"]
        self.assertEqual(["section_header", "text"], [block["label"] for block in first_units])
        self.assertFalse(any(block.get("layout_ambiguous") for batch in raw for block in batch["blocks"]))
        self.assertEqual(TITLE, first_units[1]["section"])
        self.assertGreaterEqual(first_units[1]["bbox"]["b"] / 800, .65)
        self.assertLessEqual(raw[1]["blocks"][0]["bbox"]["t"] / 800, .35)
        self.assertEqual("TOPLEFT", first_units[1]["bbox"]["coord_origin"])
        batches = self.batches(output.events)
        self.assertEqual([TITLE], [block["text"] for block in batches[0]["blocks"]])
        carry = batches[0]["resumeState"]
        self.assertEqual(FIRST, carry["pending"]["text"])
        self.assertEqual([1], carry["pending"]["pages"])
        self.assertEqual([TITLE], carry["heading"])
        merged = batches[1]["blocks"][0]
        self.assertEqual(FIRST + " " + SECOND, merged["text"])
        self.assertEqual([1, 2], merged["pages"])
        self.assertEqual([FIRST, SECOND], [part["text"] for part in merged["source_fragments"]])
        self.assertTrue(all("bbox" in part for part in merged["source_fragments"]))
        self.assertIsNone(batches[1]["resumeState"]["pending"])
        self.assertEqual("complete", output.events[-1]["type"])
        resumed = self.convert(config=settings(resume_pages=1, resume_batches=1,
                                              resume_state=json.loads(json.dumps(carry))))
        self.assertEqual(batches[1], self.batches(resumed.events)[0])
        final_resume = self.convert(config=settings(resume_pages=2, resume_batches=2,
                                                   resume_state=batches[1]["resumeState"]))
        self.assertEqual([], self.batches(final_resume.events))
        self.assertEqual("complete", final_resume.events[-1]["type"])

    def test_new_chapter_and_finished_sentence_are_not_joined(self):
        variants = [
            (FIRST + ".", [(40, 750, SECOND)]),
            (FIRST, [(40, 750, "Chapter 2 Independent requirements"), (40, 720, SECOND)]),
        ]
        for first, second_page in variants:
            with self.subTest(first=first, next_heading=second_page[0][2]):
                output = self.convert([[(40, 750, TITLE), (40, 55, first)], second_page])
                blocks = [block for batch in self.batches(output.events) for block in batch["blocks"]]
                self.assertFalse(any(block.get("continuity") for block in blocks))
                self.assertTrue(all(len(block["pages"]) == 1 for block in blocks))
                texts = [block["text"] for block in blocks]
                self.assertIn(first, texts)
                self.assertIn(SECOND, texts)

    def test_sparse_page_and_ambiguous_rotated_layout_keep_all_native_text(self):
        pages = [[(40, 750, TITLE), (40, 55, FIRST)], [(40, 750, "sparse text")]]
        output = self.convert(pages, rotations={0: 90})
        raw = self.batches(output.raw)
        self.assertTrue(raw[0]["blocks"][0]["layout_ambiguous"])
        with pdfium.PdfDocument(self.file) as document:
            expected = []
            for page in document:
                with closing(page):
                    with closing(page.get_textpage()) as textpage:
                        expected.append(textpage.get_text_bounded().replace("\x00", "").strip())
        actual = [block["text"] for batch in self.batches(output.events) for block in batch["blocks"]]
        self.assertEqual(expected, actual)
        self.assertEqual([1], raw[0]["blocks"][0]["pages"])
        self.assertFalse(any(block.get("continuity") for batch in self.batches(output.events) for block in batch["blocks"]))

    def test_real_overlapping_rectangles_use_lossless_page_fallback(self):
        lines = ["First overlapping native line has enough characters.",
                 "second overlapping native line has enough characters."]
        output = self.convert([[(40, 55, line) for line in lines], [(40, 750, SECOND)]])
        raw = self.batches(output.raw)
        # PDFium returns two rectangles for one synthesized text line. Coordinates
        # cannot safely be assigned to paragraphs, so preserve its complete text.
        block = raw[0]["blocks"][0]
        self.assertTrue(block["layout_ambiguous"])
        self.assertNotIn("bbox", block)
        self.assertTrue(all(line in block["text"] for line in lines))
        batches = self.batches(output.events)
        self.assertIsNone(batches[0]["resumeState"]["pending"])
        self.assertEqual(block["text"], batches[0]["blocks"][0]["text"])
        self.assertEqual([1], batches[0]["blocks"][0]["pages"])
        self.assertEqual(SECOND, batches[1]["blocks"][0]["text"])
        self.assertFalse(any(block.get("continuity") for batch in batches for block in batch["blocks"]))


if __name__ == "__main__":
    unittest.main()
