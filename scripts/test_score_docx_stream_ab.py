import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location("docx_score", Path(__file__).with_name("score-docx-stream-ab.py"))
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


def gold(paragraphs=(), tables=()):
    units = []
    for i, text in enumerate(paragraphs):
        units.append({"source": f"p{i}", "text": text, "nonempty_run_count": 2,
                      "heading_level": None, "expected_section": []})
    return {"paragraphs": units, "tables": list(tables), "unsupported": [],
            "ordered_nodes": [{"kind": "paragraph", "text": p["text"]} for p in units] +
                             [{"kind": "table", "table_index": i} for i, _ in enumerate(tables)]}


class DocxScoreTests(unittest.TestCase):
    def test_whitespace_only_normalization_keeps_meaningful_characters(self):
        self.assertEqual("A1.①", s.normalize(" A\t1.\n① "))
        self.assertNotEqual(s.normalize("①"), s.normalize("1"))
        self.assertNotEqual(s.normalize("A"), s.normalize("a"))

    def test_run_fragmentation_requires_complete_original_paragraph(self):
        source = gold(["截止日期2026年4月30日18:00。"])
        good = [{"type": "batch", "blocks": [{"text": "截止日期2026年4月30日18:00。", "label": "text"}]}, {"type": "complete"}]
        bad = copy.deepcopy(good)
        bad[0]["blocks"][0]["text"] = "截止日期2026年4月日18:00。"
        self.assertEqual(1, s.score_events(source, good)["multi_run_paragraph_complete"]["matched"])
        self.assertEqual(0, s.score_events(source, bad)["paragraph_text_retained"]["matched"])

    def test_adjacent_split_is_retained_but_single_paragraph_fragmentation_is_visible(self):
        source = gold(["Alpha beta gamma."])
        events = [{"type": "batch", "blocks": [{"text": "Alpha beta"}, {"text": " gamma."}]}]
        result = s.score_events(source, events)
        self.assertEqual(1, result["paragraph_text_retained"]["matched"])
        self.assertEqual(0, result["paragraph_in_single_block"]["matched"])

    def test_reordered_and_duplicated_paragraphs_are_detected(self):
        source = gold(["First sentence.", "Second sentence."])
        events = [{"type": "batch", "blocks": [{"text": "Second sentence."}, {"text": "First sentence."}, {"text": "First sentence."}]}]
        result = s.score_events(source, events)
        self.assertEqual(2, result["paragraph_text_retained"]["matched"])
        self.assertLess(result["paragraph_order_preserved"]["matched"], 2)
        self.assertEqual(1, len(result["excess_exact_paragraph_blocks"]))

    def table(self):
        xml = '''<w:tbl xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid>
          <w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>
           <w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr>
          <w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge/></w:tcPr><w:p/></w:tc>
           <w:tc><w:p><w:r><w:t>C</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'''
        return s.table_gold(ET.fromstring(xml), "table0", {}, [])

    def test_xml_vertical_and_horizontal_merge_reference_is_independent(self):
        table = self.table()
        self.assertEqual((2, 3, 3), (table["num_rows"], table["num_cols"], len(table["cells"])))
        anchor = table["cells"][0]
        self.assertEqual((2, 2, "A"), (anchor["row_span"], anchor["col_span"], anchor["text"]))

    def test_table_values_without_correct_row_relationships_do_not_pass(self):
        table = self.table()
        observed = {"num_rows": 2, "num_cols": 3, "table_cells": copy.deepcopy(table["cells"])}
        observed["table_cells"][1]["text"], observed["table_cells"][2]["text"] = "C", "B"
        result = s.score_events(gold(tables=[table]), [{"type": "batch", "blocks": [
            {"label": "table", "text": "A B C", "table_data": observed}]}])
        self.assertEqual(3, result["table_values"]["matched"])
        self.assertEqual(1, result["table_cells_text_and_spans"]["matched"])

    def test_heading_outline_can_be_inherited_from_style(self):
        styles = {"Parent": {"outline": "1"}, "Child": {"base": "Parent"}}
        paragraph = ET.fromstring('''<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:pPr><w:pStyle w:val="Child"/></w:pPr><w:r><w:t>Heading</w:t></w:r></w:p>''')
        self.assertEqual(2, s.paragraph_info(paragraph, "p", styles)["heading_level"])

    def test_generated_table_numbering_is_separate_from_body_and_span_fidelity(self):
        cell = {"text": "Required", "start_row_offset_idx": 0, "end_row_offset_idx": 1,
                "start_col_offset_idx": 0, "end_col_offset_idx": 1, "row_span": 1, "col_span": 1,
                "text_pattern": r"(?:1\.)?Required",
                "numbered_paragraphs": [{"number_label": "1.", "text": "Required"}]}
        table = {"source": "table", "num_rows": 1, "num_cols": 1, "cells": [cell]}
        def score(text, row=0):
            actual = dict(cell, text=text, start_row_offset_idx=row, end_row_offset_idx=row + 1)
            return s.score_events(gold(tables=[table]), [{"type": "batch", "blocks": [
                {"label": "table", "text": text, "table_data": {"num_rows": 1, "num_cols": 1, "table_cells": [actual]}}]}])
        numbered = score("1. Required")
        self.assertEqual(1, numbered["table_cells_text_and_spans"]["matched"])
        self.assertEqual(0, numbered["table_raw_exact_cells_text_and_spans"]["matched"])
        self.assertEqual(1, numbered["table_numbering_label"]["matched"])
        unnumbered = score("Required")
        self.assertEqual(1, unnumbered["table_cells_text_and_spans"]["matched"])
        self.assertEqual(0, unnumbered["table_numbering_label"]["matched"])
        self.assertEqual(0, score("2. Required")["table_cells_text_and_spans"]["matched"])
        self.assertEqual(0, score("1. Required", row=1)["table_cells_text_and_spans"]["matched"])

    def test_raw_docling_marker_is_not_misreported_as_parser_numbering_loss(self):
        source = gold(["Complete the evidence."])
        source["paragraphs"][0].update(numbering_status="SUPPORTED", number_label="1.")
        events = [{"type": "batch", "blocks": [{"text": "Complete the evidence.", "label": "list_item"}],
                   "doclingDocument": {"texts": [{"text": "Complete the evidence.", "marker": "1."}]}}]
        result = s.score_events(source, events)
        self.assertEqual(1, result["supported_numbering_label"]["matched"])
        self.assertEqual(0, result["normalized_block_numbering_label"]["matched"])

    def test_section_names_in_wrong_order_do_not_pass(self):
        source = gold(["Evidence"])
        source["paragraphs"][0]["expected_section"] = ["Root", "Subsection"]
        result = s.score_events(source, [{"type": "batch", "blocks": [
            {"text": "Evidence", "section": "Subsection / Root"}]}])
        self.assertEqual(0, result["explicit_section_path"]["matched"])

    def test_start_override_takes_precedence_over_full_level_start(self):
        node = ET.fromstring('''<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum><w:num w:numId="2"><w:abstractNumId w:val="1"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="15"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:lvlOverride></w:num></w:numbering>''')
        self.assertEqual(15, s.numbering_for(node)["2"][0]["start"])

    def test_resume_compares_every_batch_field_not_only_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full = [{"type": "batch", "blocks": [{"text": "A", "ref": "first"}]},
                    {"type": "batch", "blocks": [{"text": "B", "ref": "second"}]}, {"type": "complete"}]
            (root / "stream-events.jsonl").write_text("\n".join(json.dumps(e) for e in full), encoding="utf-8")
            suffix = copy.deepcopy(full[1:])
            suffix[0]["blocks"][0]["ref"] = "changed-source"
            (root / "resume.jsonl").write_text("\n".join(json.dumps(e) for e in suffix), encoding="utf-8")
            result = s.resume_scores(root, {"resumeChecks": [{"mode": "stream", "afterBatch": 1, "eventsPath": "resume.jsonl"}]})
            self.assertEqual("FAIL", result["status"])
            self.assertEqual(0, result["checks"][0]["first_different_event"])


if __name__ == "__main__":
    unittest.main()
