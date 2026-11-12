"""Real chunk construction after boundary repair; no inference or external store."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docling"))
from chunking_document import attach_chunking_document
from continuity import merge_boundary

try:
    from docling_core.types.doc import DoclingDocument, DocItemLabel, ProvenanceItem, Size
    from tokenizers import Tokenizer, models, pre_tokenizers, processors
    from transformers import PreTrainedTokenizerFast
    CORE_AVAILABLE = True
except ImportError:
    CORE_AVAILABLE = False


def prov(page, bottom=False):
    return {"page_no": page, "charspan": [0, 10],
            "bbox": {"l": 30, "t": 650 if bottom else 40, "r": 500,
                     "b": 810 if bottom else 200, "coord_origin": "TOPLEFT"}}


def fragment(page, ref, text, bottom=False, **extras):
    return {"origin_id": f"page-{page}-{ref}", "ref": ref, "batch_index": page - 1,
            "pages": [page], "text": text, "prov": [prov(page, bottom)],
            "bbox": prov(page, bottom)["bbox"], "page_size": {"width": 595, "height": 842}, **extras}


def tiny_tokenizer():
    # An in-memory fast tokenizer exercises the actual HuggingFaceTokenizer
    # adapter and semchunk without a network download or neural model weights.
    backend = Tokenizer(models.WordLevel(vocab={"[UNK]": 0, "[CLS]": 1, "[SEP]": 2}, unk_token="[UNK]"))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    backend.post_processor = processors.TemplateProcessing(single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 1), ("[SEP]", 2)])
    return PreTrainedTokenizerFast(tokenizer_object=backend, unk_token="[UNK]", cls_token="[CLS]", sep_token="[SEP]", model_max_length=512)


@unittest.skipUnless(CORE_AVAILABLE, "docling-core[chunking] is not installed")
class ContinuityChunkingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tokenizer = tiny_tokenizer()

    def raw(self, body="RAW_OLD_TAIL_MUST_NOT_LEAK"):
        doc = DoclingDocument(name="raw-converter-output")
        doc.add_page(page_no=2, size=Size(width=595, height=842))
        doc.add_text(label=DocItemLabel.TEXT, text=body, prov=ProvenanceItem.model_validate(prov(2)))
        return doc.export_to_dict()

    def chunks(self, batch, **options):
        request = app.ChunkRequest(document_id="continuity-doc", owner_id=1, index_version="continuity-v1",
                                   batch_index=1, batch=batch, merge_peers=False, **options)
        with patch.object(app, "embedding_models", return_value=(self.tokenizer, None)):
            return app.chunks_for(request)

    def assert_compact_sources(self, chunk):
        source = chunk["sources"][0]
        self.assertEqual([1, 2], source["pages"])
        self.assertEqual([0, 1], [f["batch_index"] for f in source["fragments"]])
        self.assertEqual(["#/texts/7", "#/texts/0"], [f["ref"] for f in source["fragments"]])
        self.assertEqual("b0:#/texts/7, b1:#/texts/0", source["ref"])
        for value in source["fragments"]:
            self.assertNotIn("text", value)
            self.assertNotIn("table_data", value)
            self.assertNotIn("prov", value)
            self.assertNotIn("raw_source", value)
            self.assertIn("bbox", value)
            self.assertIn("page_size", value)

    def test_docx_rich_locations_survive_without_copying_ocr_metadata_to_vector_rows(self):
        source={"ref":"docx:body:2/image:x","node_ref":"docx:body:2/image:x","body_node":2,
                "batch_index":0,"parse_unit":"DOCX_BODY_NODE","source_part":"word/header1.xml",
                "part_kind":"header","content_kind":"image_ocr","text":"TOTAL 12345","pages":[],
                "images":[{"words":[{"text":"TOTAL","confidence":98,"bbox":[1,2,3,4]}]}]}
        batch={"doclingDocument":self.raw(),"blocks":[{"ref":source['ref'],"label":"text",
            "section":"Header","text":"TOTAL 12345","pages":[],"source_fragments":[source]}]}
        attach_chunking_document(batch)
        chunks=self.chunks(batch)
        self.assertEqual(len(chunks),1)
        fragment=chunks[0]['sources'][0]['fragments'][0]
        self.assertEqual(fragment['source_part'],'word/header1.xml')
        self.assertEqual(fragment['content_kind'],'image_ocr')
        self.assertEqual(fragment['node_ref'],'docx:body:2/image:x')
        self.assertNotIn('images',fragment)
        self.assertNotIn('words',fragment)

    def test_canonical_document_excludes_deferred_tail_from_raw_document(self):
        raw = DoclingDocument(name="raw")
        raw.add_text(label=DocItemLabel.TEXT, text="PUBLISHED_BODY")
        raw.add_text(label=DocItemLabel.TEXT, text="DEFERRED_TAIL_MUST_NOT_LEAK")
        batch = {"doclingDocument": raw.export_to_dict(), "blocks": [
            {"ref": "#/texts/0", "text": "PUBLISHED_BODY", "label": "text", "pages": [], "section": "Policy"}]}
        attach_chunking_document(batch)
        chunks = self.chunks(batch)
        self.assertEqual(["PUBLISHED_BODY"], [c["content"] for c in chunks])
        self.assertIn("DEFERRED_TAIL_MUST_NOT_LEAK", str(batch["doclingDocument"]))
        self.assertNotIn("DEFERRED_TAIL_MUST_NOT_LEAK", str(chunks))

    def test_empty_canonical_document_does_not_fall_back_to_raw_tail(self):
        batch = {"doclingDocument": self.raw(), "blocks": []}
        attach_chunking_document(batch)
        self.assertEqual([], self.chunks(batch))

    def test_joined_paragraph_keeps_both_original_sources_without_metadata_expansion(self):
        left, right = "The graduation requirement is", "a minimum of 160 credits."
        batch = {"doclingDocument": self.raw(), "blocks": [{
            "ref": "joined", "text": left + " " + right, "label": "text", "section": "Graduation",
            "pages": [1, 2], "source_fragments": [
                fragment(1, "#/texts/7", left, bottom=True, raw_source={"huge": "data"}),
                fragment(2, "#/texts/0", right)]}]}
        attach_chunking_document(batch)
        chunks = self.chunks(batch)
        self.assertEqual(1, len(chunks))
        self.assertEqual(left + " " + right, chunks[0]["content"])
        self.assertEqual((1, 2), (chunks[0]["page_start"], chunks[0]["page_end"]))
        self.assert_compact_sources(chunks[0])

    def table(self, page, first, last):
        values = [["Item", "Description"]] + [[f"Row{number:03}", " ".join(f"value{word}" for word in range(18))]
                                                   for number in range(first, last + 1)]
        cells = []
        for row, row_values in enumerate(values):
            for col, value in enumerate(row_values):
                cells.append({"text": value, "start_row_offset_idx": row, "end_row_offset_idx": row + 1,
                              "start_col_offset_idx": col, "end_col_offset_idx": col + 1,
                              "column_header": row == 0, "row_span": 1, "col_span": 1})
        table_data = {"num_rows": len(values), "num_cols": 2, "table_cells": cells}
        text = "|Item|Description|\n|---|---|\n" + "\n".join("|" + "|".join(row) + "|" for row in values[1:])
        block = fragment(page, "#/texts/7" if page == 1 else "#/texts/0", text, bottom=page == 1)
        block.update(label="table", section="Statistics", table_data=table_data)
        return block

    def test_long_continued_table_keeps_every_row_after_actual_hybrid_splitting(self):
        left, right = self.table(1, 1, 20), self.table(2, 21, 40)
        joined = merge_boundary(left, right)
        self.assertIsNotNone(joined)
        self.assertEqual(41, joined["table_data"]["num_rows"])
        self.assertEqual(2, sum(cell["column_header"] for cell in joined["table_data"]["table_cells"]))
        batch = {"doclingDocument": self.raw(), "blocks": [joined]}
        attach_chunking_document(batch)
        chunks = self.chunks(batch)
        self.assertGreater(len(chunks), 1)
        text = "\n".join(c["content"] for c in chunks)
        for row in range(1, 41):
            self.assertEqual(1, text.count(f"Row{row:03}"), f"missing or duplicated data row {row}")
        for chunk in chunks:
            self.assertLessEqual(chunk["token_count"], 512)
            self.assert_compact_sources(chunk)
        self.assertNotIn("RAW_OLD_TAIL_MUST_NOT_LEAK", text)

    def test_unaffected_legacy_docling_keeps_original_content_and_chunk_identity(self):
        document = DoclingDocument(name="unchanged")
        document.add_page(page_no=2, size=Size(width=595, height=842))
        document.add_heading(text="Policy", level=1)
        document.add_text(label=DocItemLabel.TEXT, text="Alpha beta.", prov=ProvenanceItem.model_validate(prov(2)))
        batch = {"doclingDocument": document.export_to_dict(), "blocks": [
            {"ref": "#/texts/0", "label": "section_header", "text": "Policy", "section": "Policy", "pages": []},
            {"ref": "#/texts/1", "label": "text", "text": "Alpha beta.", "section": "Policy", "pages": [2]}]}
        chunks = self.chunks(batch)
        self.assertEqual(["Alpha beta."], [c["content"] for c in chunks])
        expected = hashlib.sha256("continuity-doc:continuity-v1:1:0:Alpha beta.".encode()).hexdigest()
        self.assertEqual(expected, chunks[0]["chunk_id"])
        self.assertEqual("b1:#/texts/1", chunks[0]["sources"][0]["ref"])
        self.assertEqual(chunks, self.chunks(copy.deepcopy(batch)))
        self.assertNotIn("chunkingDocument", batch)


if __name__ == "__main__":
    unittest.main()
