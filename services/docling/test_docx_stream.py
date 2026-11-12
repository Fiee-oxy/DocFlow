import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from docx_stream import iter_docx_batches, _format_number, W, R, M, REL


def paragraph(text, properties=""):
    return f"<w:p>{properties}<w:r><w:t>{text}</w:t></w:r></w:p>"


def cell(text="", properties=""):
    return f"<w:tc>{properties}{paragraph(text)}</w:tc>"


def row(cells, properties=""):
    return f"<w:tr>{properties}{''.join(cells)}</w:tr>"


def table(rows, columns=3):
    return "<w:tbl><w:tblGrid>" + "<w:gridCol/>" * columns + "</w:tblGrid>" + "".join(rows) + "</w:tbl>"


class DocxStreamTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "source.docx"

    def tearDown(self):
        self.temporary.cleanup()

    def package(self, body, *, styles=None, numbering=None, relationships=None, raw=None, extra=None):
        document = raw or (f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W}" xmlns:r="{R}" '
                           f'xmlns:m="{M}"><w:body>{body}<w:sectPr/></w:body></w:document>')
        with zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", document)
            if styles is not None:
                archive.writestr("word/styles.xml", f'<w:styles xmlns:w="{W}">{styles}</w:styles>')
            if numbering is not None:
                archive.writestr("word/numbering.xml", f'<w:numbering xmlns:w="{W}">{numbering}</w:numbering>')
            if relationships is not None:
                archive.writestr("word/_rels/document.xml.rels", f'<Relationships xmlns="{REL}">{relationships}</Relationships>')
            for name, contents in (extra or {}).items():
                archive.writestr(name, contents)
        return self.path

    def parse(self, **settings):
        return list(iter_docx_batches(self.path, settings))

    @staticmethod
    def batches(events):
        return [event for event in events if event["type"] == "batch"]

    @staticmethod
    def blocks(events):
        return [block for batch in DocxStreamTests.batches(events) for block in batch["blocks"]]

    def test_cross_run_text_tabs_breaks_and_external_hyperlink(self):
        self.package('<w:p><w:r><w:t xml:space="preserve">  Hello</w:t></w:r><w:r><w:t> world</w:t>'
                     '<w:tab/><w:t>one</w:t><w:br/><w:t>two</w:t></w:r>'
                     '<w:hyperlink r:id="link"><w:r><w:t> website</w:t></w:r></w:hyperlink></w:p>',
                     relationships='<Relationship Id="link" Type="' + R + '/hyperlink" Target="https://example.com" TargetMode="External"/>')
        events = self.parse()
        block = self.blocks(events)[0]
        self.assertEqual(block["text"], "  Hello world\tone\ntwo website")
        self.assertEqual(block["hyperlinks"], ["https://example.com"])
        self.assertEqual(block["pages"], [])
        self.assertEqual(block["node_ref"], "docx:body:0")
        self.assertEqual(block["parse_unit"], "DOCX_BODY_NODE")
        self.assertEqual(block["source_fragments"][0]["parse_unit"], "DOCX_BODY_NODE")
        self.assertEqual(block["source_fragments"][0]["ref"], "docx:body:0")
        self.assertTrue(block["ref"].startswith("#/texts/"))
        self.assertEqual(events[-1]["totalPages"], 0)
        self.assertFalse(events[-1]["partial"])
        self.assertEqual(events[-1]["warnings"], self.batches(events)[-1]["warnings"])

    def test_heading_style_inheritance_and_checkpoint_chapter_state(self):
        styles = ('<w:style w:styleId="H1"><w:name w:val="Heading 1"/></w:style>'
                  '<w:style w:styleId="Base"><w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style>'
                  '<w:style w:styleId="Custom"><w:basedOn w:val="Base"/></w:style>')
        body = (paragraph("Chapter", '<w:pPr><w:pStyle w:val="H1"/></w:pPr>') +
                paragraph("Section", '<w:pPr><w:pStyle w:val="Custom"/></w:pPr>') + paragraph("Evidence"))
        self.package(body, styles=styles)
        batches = self.batches(self.parse(docx_batch_nodes=1))
        self.assertEqual(batches[1]["blocks"][0]["heading_level"], 2)
        self.assertEqual(batches[2]["blocks"][0]["section"], "Chapter / Section")
        self.assertEqual(batches[2]["resumeState"]["heading"], ["Chapter", "Section"])

    def test_grid_span_and_vertical_merge_keep_complete_table(self):
        rows = [row([cell("A"), cell("B"), cell("C")], '<w:trPr><w:tblHeader/></w:trPr>'),
                row([cell("AB", '<w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'), cell("C1")]),
                row([cell("Merged", '<w:tcPr><w:vMerge w:val="restart"/></w:tcPr>'), cell("B2"), cell("C2")]),
                row([cell("", '<w:tcPr><w:vMerge/></w:tcPr>'), cell("B3"), cell("C3")])]
        self.package(table(rows))
        blocks = self.blocks(self.parse(docx_batch_chars=10))
        self.assertEqual(len(blocks), 1)
        data = blocks[0]["table_data"]
        self.assertEqual((data["num_rows"], data["num_cols"]), (4, 3))
        self.assertEqual(sum(cell["column_header"] for cell in data["table_cells"]), 3)
        horizontal = next(cell for cell in data["table_cells"] if cell["text"] == "AB")
        vertical = next(cell for cell in data["table_cells"] if cell["text"] == "Merged")
        self.assertEqual(horizontal["col_span"], 2)
        self.assertEqual((vertical["row_span"], vertical["end_row_offset_idx"]), (2, 4))
        self.assertEqual(len(vertical["source_refs"]), 2)
        self.assertEqual(blocks[0]["table_id"], "docx:body:0")

    def test_legacy_horizontal_merge_is_explicitly_grouped(self):
        self.package(table([row([cell("A", '<w:tcPr><w:hMerge w:val="restart"/></w:tcPr>'),
                                      cell("B", '<w:tcPr><w:hMerge/></w:tcPr>'), cell("C")])]))
        cells = self.blocks(self.parse())[0]["table_data"]["table_cells"]
        self.assertEqual(cells[0]["text"], "A\nB")
        self.assertEqual(cells[0]["col_span"], 2)

    def numbering(self):
        return ('<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
                '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>'
                '<w:num w:numId="7"><w:abstractNumId w:val="0"/></w:num>')

    def test_numbered_lists_preserve_markers_across_batches_and_resume(self):
        props = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="7"/></w:numPr></w:pPr>'
        self.package("".join(paragraph(text, props) for text in ["one", "two", "three"]), numbering=self.numbering())
        events = self.parse(docx_batch_nodes=1)
        self.assertEqual([block["marker"] for block in self.blocks(events)], ["1.", "2.", "3."])
        self.assertTrue(all(block["label"] == "list_item" for block in self.blocks(events)))
        first = self.batches(events)[0]["resumeState"]
        resumed = self.parse(docx_batch_nodes=1, resume_state=json.loads(json.dumps(first)))
        self.assertEqual([block["marker"] for block in self.blocks(resumed)], ["2.", "3."])

    def test_chinese_counting_format_range_and_internal_zero_rules(self):
        expected = {0: "零", 1: "一", 10: "十", 11: "十一", 20: "二十", 101: "一百零一",
                    110: "一百一十", 1001: "一千零一", 1010: "一千零一十", 9999: "九千九百九十九"}
        for value, text in expected.items():
            with self.subTest(value=value):
                self.assertEqual(_format_number(value, "chineseCounting"), text)
        with self.assertRaisesRegex(ValueError, "NUMBER_FORMAT_RANGE"):
            _format_number(10000, "chineseCounting")

    def test_chinese_numbering_resume_and_out_of_range_strict_failure(self):
        props = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="7"/></w:numPr></w:pPr>'
        definition = self.numbering().replace('w:val="decimal"', 'w:val="chineseCounting"').replace('w:val="1"', 'w:val="99"')
        self.package("".join(paragraph(text, props) for text in ["第一条", "第二条", "第三条"]), numbering=definition)
        full = self.parse(docx_batch_nodes=1)
        self.assertEqual([block["marker"] for block in self.blocks(full)], ["九十九.", "一百.", "一百零一."])
        state = self.batches(full)[0]["resumeState"]
        resumed = self.parse(docx_batch_nodes=1, resume_state=json.loads(json.dumps(state)))
        self.assertEqual(self.batches(resumed), self.batches(full)[1:])
        self.package(paragraph("out of range", props), numbering=definition.replace('w:val="99"', 'w:val="10000"'))
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_NUMBER_FORMAT_RANGE"):
            self.parse()

    def test_all_json_roundtripped_checkpoints_match_uninterrupted_output(self):
        styles = '<w:style w:styleId="H"><w:name w:val="Heading 1"/></w:style>'
        self.package(paragraph("Title", '<w:pPr><w:pStyle w:val="H"/></w:pPr>') +
                     paragraph("A") + paragraph("") + table([row([cell("a"), cell("b"), cell("c")])]) +
                     paragraph("last"), styles=styles)
        events = self.parse(docx_batch_nodes=2)
        batches = self.batches(events)
        for index, batch in enumerate(batches):
            state = json.loads(json.dumps(batch["docxResumeState"]))
            resumed = self.parse(docx_batch_nodes=2, resume_state=state)
            self.assertEqual(self.batches(resumed), batches[index + 1:])
            self.assertEqual(resumed[-1], events[-1])
            self.assertEqual(state["nextBodyNode"], batch["endNode"])
            self.assertEqual(state["nextBatch"], batch["batchIndex"] + 1)
        self.assertEqual(events[-1]["totalBodyNodes"], 6)
        self.assertEqual(batches[-1]["endNode"], 6)

    def test_file_or_parser_configuration_change_rejects_checkpoint(self):
        self.package(paragraph("one") + paragraph("two"))
        state = self.batches(self.parse(docx_batch_nodes=1))[0]["resumeState"]
        with self.assertRaisesRegex(ValueError, "parameters changed"):
            self.parse(docx_batch_nodes=2, resume_state=state)
        self.package(paragraph("changed") + paragraph("two"))
        with self.assertRaisesRegex(ValueError, "file or parser parameters changed"):
            self.parse(docx_batch_nodes=1, resume_state=state)

    def test_missing_node_checkpoint_is_not_accepted_as_page_resume(self):
        self.package(paragraph("text"))
        with self.assertRaisesRegex(ValueError, "node checkpoint required"):
            self.parse(resume_batches=1)

    def test_oversized_table_or_paragraph_fails_instead_of_splitting_source(self):
        self.package(table([row([cell("a"), cell("b"), cell("c")])] * 4))
        with self.assertRaisesRegex(ValueError, "TABLE_LIMIT"):
            self.parse(docx_max_table_cells=10)
        self.package(paragraph("x" * 101))
        with self.assertRaisesRegex(ValueError, "NODE_CHAR_LIMIT"):
            self.parse(docx_max_node_chars=100)
        self.package(paragraph("x" * 500))
        events = self.parse(docx_batch_chars=20, docx_max_node_chars=1000)
        self.assertEqual(self.blocks(events)[0]["text"], "x" * 500)

    def test_unsupported_features_fail_strict_and_are_visible_if_explicitly_degraded(self):
        examples = [('<w:p><w:r><w:drawing/></w:r></w:p>', "DRAWING_WITHOUT_IMAGE_OR_TEXT"),
                    ('<w:p><w:r><w:object/></w:r></w:p>', "EMBEDDED_OBJECT"),
                    ('<w:altChunk/>', "ALT_CHUNK")]
        for body, code in examples:
            with self.subTest(feature=code):
                self.package(body)
                with self.assertRaisesRegex(ValueError, "UNSUPPORTED_" + code):
                    self.parse()
        self.package('<w:p><w:r><w:object/><w:t>visible text</w:t></w:r></w:p>')
        batch = self.batches(self.parse(docx_strict=False))[0]
        self.assertTrue(batch["partial"])
        self.assertIn("EMBEDDED_OBJECT", " ".join(batch["warnings"]))
        self.assertEqual(batch["blocks"][0]["text"], "visible text")

    def test_missing_style_numbering_and_hyperlink_dependencies_reject(self):
        bodies = [paragraph("style", '<w:pPr><w:pStyle w:val="Missing"/></w:pPr>'),
                  paragraph("list", '<w:pPr><w:numPr><w:numId w:val="99"/></w:numPr></w:pPr>'),
                  '<w:p><w:hyperlink r:id="missing"><w:r><w:t>link</w:t></w:r></w:hyperlink></w:p>']
        for body in bodies:
            self.package(body)
            with self.assertRaisesRegex(ValueError, "DEPENDENCY_MISSING"):
                self.parse()

    def test_style_cycles_are_rejected(self):
        self.package(paragraph("body", '<w:pPr><w:pStyle w:val="A"/></w:pPr>'),
                     styles='<w:style w:styleId="A"><w:basedOn w:val="B"/></w:style>'
                            '<w:style w:styleId="B"><w:basedOn w:val="A"/></w:style>')
        with self.assertRaisesRegex(ValueError, "cyclic"):
            self.parse()

    def test_explicit_degradation_warning_survives_resume(self):
        self.package('<w:p><w:r><w:object/><w:t>attachment text</w:t></w:r></w:p>' + paragraph("later"))
        events = self.parse(docx_batch_nodes=1, docx_strict=False)
        state = self.batches(events)[0]["resumeState"]
        resumed = self.parse(docx_batch_nodes=1, docx_strict=False, resume_state=json.loads(json.dumps(state)))
        self.assertTrue(self.batches(resumed)[0]["partial"])
        self.assertEqual(self.batches(resumed), self.batches(events)[1:])
        self.assertTrue(resumed[-1]["partial"])
        self.assertEqual(resumed[-1]["warnings"], self.batches(resumed)[-1]["warnings"])

    def test_orphan_vertical_merge_is_rejected(self):
        self.package(table([row([cell("", '<w:tcPr><w:vMerge/></w:tcPr>'), cell("b"), cell("c")])]))
        with self.assertRaisesRegex(ValueError, "orphan/misaligned"):
            self.parse()

    def test_zip_and_xml_resource_guards(self):
        self.package(paragraph("text"), extra={"other": "data"})
        with self.assertRaisesRegex(ValueError, "ZIP_LIMIT"):
            self.parse(docx_max_zip_entries=1)
        with self.assertRaisesRegex(ValueError, "XML_LIMIT"):
            self.parse(docx_max_xml_bytes=10)
        with self.assertRaisesRegex(ValueError, "NODE_XML_LIMIT"):
            self.parse(docx_max_node_xml_bytes=20)
        self.package(paragraph("body"), styles='<w:style w:styleId="Long"/>')
        with self.assertRaisesRegex(ValueError, "METADATA_LIMIT"):
            self.parse(docx_max_metadata_bytes=10)

    def test_dtd_entity_and_utf16_are_rejected_without_expansion(self):
        xml = f'<!DOCTYPE x [<!ENTITY x "expanded">]><w:document xmlns:w="{W}"><w:body>{paragraph("&x;")}</w:body></w:document>'
        self.package("", raw=xml)
        with self.assertRaisesRegex(ValueError, "UNSAFE_XML"):
            self.parse()
        self.package("", raw=(f'<w:document xmlns:w="{W}"><w:body/></w:document>').encode("utf-16"))
        with self.assertRaisesRegex(ValueError, "UNSAFE_XML"):
            self.parse()

    def test_malformed_xml_fails_and_trailing_empty_nodes_are_checkpointed(self):
        self.package("", raw=f'<w:document xmlns:w="{W}"><w:body><w:p>')
        with self.assertRaisesRegex(ValueError, "INVALID_XML"):
            self.parse()
        self.package(paragraph("first") + paragraph(""))
        batches = self.batches(self.parse(docx_batch_nodes=1))
        self.assertEqual([batch["endNode"] for batch in batches], [1, 2, 3])
        self.assertEqual(batches[-1]["blocks"], [])
        self.assertEqual(batches[-1]["resumeState"]["nextBodyNode"], 3)

    def test_top_level_bookmarks_advance_cursor_and_resume_without_losing_paragraphs(self):
        self.package(paragraph("first") + '<w:bookmarkStart w:id="0" w:name="anchor"/>' +
                     paragraph("middle") + '<w:bookmarkEnd w:id="0"/>' + paragraph("last"))
        full = self.parse(docx_batch_nodes=2)
        batches = self.batches(full)
        self.assertEqual([block["text"] for block in self.blocks(full)], ["first", "middle", "last"])
        self.assertEqual([block["body_node"] for block in self.blocks(full)], [0, 2, 4])
        self.assertEqual([batch["endNode"] for batch in batches], [2, 4, 6])
        self.assertEqual(full[-1]["totalBodyNodes"], 6)
        for index, batch in enumerate(batches):
            state = json.loads(json.dumps(batch["resumeState"]))
            resumed = self.parse(docx_batch_nodes=2, resume_state=state)
            self.assertEqual(self.batches(resumed), batches[index + 1:])
            self.assertEqual(resumed[-1], full[-1])
        self.package('<w:bookmarkEnd w:id="0"><w:r><w:t>must not disappear</w:t></w:r></w:bookmarkEnd>')
        with self.assertRaisesRegex(ValueError, "DOCX_INVALID_MARKER"):
            self.parse()

    def test_first_batch_is_yielded_before_later_unsupported_content(self):
        self.package("".join(paragraph(f"node {n}") for n in range(400)) + '<w:p><w:r><w:drawing/></w:r></w:p>')
        stream = iter_docx_batches(self.path, {"docx_batch_nodes": 2})
        self.assertEqual(next(stream)["type"], "metadata")
        first = next(stream)
        self.assertEqual(first["endNode"], 2)
        self.assertEqual([block["text"] for block in first["blocks"]], ["node 0", "node 1"])
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_DRAWING_WITHOUT_IMAGE_OR_TEXT"):
            list(stream)


if __name__ == "__main__":
    unittest.main()
