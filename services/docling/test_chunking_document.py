import copy
import unittest

from chunking_document import attach_chunking_document

try:
    from docling_core.types.doc import DoclingDocument, DocItemLabel, ProvenanceItem, Size, TableData
    CORE_AVAILABLE = True
except ImportError:
    CORE_AVAILABLE = False


def provenance(page):
    return {"page_no": page,
            "bbox": {"l": 50, "t": 700, "r": 500, "b": 650, "coord_origin": "BOTTOMLEFT"},
            "charspan": [0, 9]}


def table_data():
    cells = []
    for row, values in enumerate((("Name", "Credits"), ("Required", "160"), ("Optional", "12"))):
        for col, value in enumerate(values):
            cells.append({"text": value, "start_row_offset_idx": row, "end_row_offset_idx": row + 1,
                          "start_col_offset_idx": col, "end_col_offset_idx": col + 1,
                          "column_header": row == 0, "row_span": 1, "col_span": 1})
    return {"num_rows": 3, "num_cols": 2, "table_cells": cells}


@unittest.skipUnless(CORE_AVAILABLE, "docling-core is not installed")
class ChunkingDocumentTests(unittest.TestCase):
    def raw(self):
        document = DoclingDocument(name="original")
        document.add_page(page_no=2, size=Size(width=595, height=842))
        document.add_text(label=DocItemLabel.TEXT, text="second page", prov=ProvenanceItem.model_validate(provenance(2)))
        return document.export_to_dict()

    def event(self):
        fragments = [
            {"ref": "#/texts/7", "batch_index": 0, "pages": [1], "text": "The required graduation",
             "prov": [provenance(1)], "page_size": {"width": 595, "height": 842}},
            {"ref": "#/texts/0", "batch_index": 1, "pages": [2], "text": "credits are 160.",
             "prov": [provenance(2)], "page_size": {"width": 595, "height": 842}}]
        return {"doclingDocument": self.raw(), "blocks": [
            {"ref": "merged", "label": "text", "section": "Graduation",
             "text": "The required graduation credits are 160.", "pages": [1, 2], "source_fragments": fragments}]}

    def test_raw_document_and_original_fragments_are_unchanged(self):
        event = self.event()
        raw = copy.deepcopy(event["doclingDocument"])
        fragments = copy.deepcopy(event["blocks"][0]["source_fragments"])
        attach_chunking_document(event)
        self.assertEqual(raw, event["doclingDocument"])
        self.assertEqual(fragments, event["blocks"][0]["source_fragments"])
        document = DoclingDocument.model_validate(event["chunkingDocument"])
        self.assertEqual("The required graduation credits are 160.", document.texts[0].text)
        self.assertEqual([1, 2], [p.page_no for p in document.texts[0].prov])
        self.assertEqual({1, 2}, set(document.pages))
        self.assertEqual("#/texts/0", event["blocks"][0]["ref"])
        self.assertEqual(50, document.texts[0].prov[0].bbox.l)
        self.assertEqual(["#/texts/7", "#/texts/0"], [f["ref"] for f in fragments])

    def test_structured_table_headers_dimensions_and_provenance_survive(self):
        event = self.event()
        event["blocks"][0].update(label="table", table_data=table_data(), text="original markdown")
        attach_chunking_document(event)
        document = DoclingDocument.model_validate(event["chunkingDocument"])
        table = document.tables[0]
        self.assertEqual((3, 2), (table.data.num_rows, table.data.num_cols))
        self.assertTrue(table.data.table_cells[0].column_header)
        self.assertEqual("160", table.data.table_cells[3].text)
        self.assertEqual([1, 2], [p.page_no for p in table.prov])
        self.assertEqual("#/tables/0", event["blocks"][0]["ref"])

    def test_missing_structured_table_data_retains_markdown_without_inventing_cells(self):
        event = self.event()
        event["blocks"][0].update(label="table", text="|Name|Credits|\n|---|---|\n|Required|160|")
        attach_chunking_document(event)
        document = DoclingDocument.model_validate(event["chunkingDocument"])
        self.assertEqual([], document.tables)
        self.assertIn("|Required|160|", document.texts[0].text)
        self.assertEqual("table", event["blocks"][0]["label"])

    def test_real_heading_levels_and_list_labels_survive_without_repeated_headings(self):
        event = self.event()
        event["blocks"] = [
            {"ref": "old-heading", "text": "Graduation", "label": "section_header", "heading_level": 2, "pages": []},
            event["blocks"][0],
            {"ref": "old-list", "text": "Complete internship", "label": "list_item", "pages": []}]
        attach_chunking_document(event)
        document = DoclingDocument.model_validate(event["chunkingDocument"])
        self.assertEqual(3, len(document.texts))
        self.assertEqual(2, document.texts[0].level)
        self.assertEqual("list_item", document.texts[2].label.value)
        self.assertEqual(1, sum(t.label.value == "section_header" for t in document.texts))

    def test_existing_original_provenance_is_available_without_normalization_metadata(self):
        event = {"doclingDocument": self.raw(), "blocks": [
            {"ref": "#/texts/0", "text": "second page", "label": "text", "pages": [2]}]}
        attach_chunking_document(event)
        self.assertEqual(2, event["chunkingDocument"]["texts"][0]["prov"][0]["page_no"])
        self.assertEqual("#/texts/0", event["blocks"][0]["source_fragments"][0]["ref"])

    def test_actual_hybrid_chunker_receives_joined_text_and_structured_table(self):
        from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
        from docling_core.transforms.chunker.tokenizer.base import BaseTokenizer

        class TestTokenizer(BaseTokenizer):
            def count_tokens(self, text): return len(text.split())
            def get_max_tokens(self): return 350
            def get_tokenizer(self): return self
            def encode(self, text, **kwargs): return list(range(len(text.split())))

        event = self.event()
        event["blocks"].append({"ref": "table", "label": "table", "section": "Graduation",
                                "text": "", "pages": [1, 2], "table_data": table_data(),
                                "source_fragments": copy.deepcopy(event["blocks"][0]["source_fragments"])})
        attach_chunking_document(event)
        document = DoclingDocument.model_validate(event["chunkingDocument"])
        chunks = list(HybridChunker(tokenizer=TestTokenizer(), merge_peers=False).chunk(dl_doc=document))
        text = "\n".join(c.text for c in chunks)
        self.assertIn("The required graduation credits are 160.", text)
        self.assertIn("Required", text)
        self.assertIn("160", text)
        refs = {i.self_ref for c in chunks for i in c.meta.doc_items}
        self.assertIn("#/texts/0", refs)
        self.assertIn("#/tables/0", refs)


class NativeFastTests(unittest.TestCase):
    def test_native_fast_does_not_create_a_docling_view(self):
        event = {"parser": "FAST", "blocks": [{"text": "native", "pages": [1]}]}
        self.assertIs(event, attach_chunking_document(event))
        self.assertNotIn("chunkingDocument", event)


if __name__ == "__main__":
    unittest.main()
