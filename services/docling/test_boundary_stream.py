import copy
import json
import unittest
from unittest.mock import patch
from boundary_stream import BoundaryAssembler


def settings(**changes):
    return dict(parse_mode="FAST", fast_batch_pages=1, batch_pages=1, resume_pages=0,
                resume_batches=0, ocr_enabled=False, **changes)


def block(page, text, *, bottom=False, section="Chapter 1", label="text"):
    return {"ref":f"page-{page}","text":text,"pages":[page],"label":label,"section":section,
            "bbox":{"l":50,"t":720 if bottom else 50,"r":550,"b":740 if bottom else 70,"coord_origin":"TOPLEFT"},
            "page_size":{"width":612,"height":792}}


def event(page, blocks, total=2):
    return {"type":"batch","startPage":page,"endPage":page,"totalPages":total,
            "blocks":blocks,"headingState":["Chapter 1"],"warnings":[]}


class BoundaryStreamTests(unittest.TestCase):
    def test_deferred_tail_is_not_published_twice_and_resume_matches_full(self):
        first=event(1,[block(1,"The graduation requirement is",bottom=True)])
        second=event(2,[block(2,"one hundred and sixty credits.")])
        assembler=BoundaryAssembler(settings())
        saved=assembler.process(first)
        self.assertEqual([],saved["blocks"])
        self.assertEqual(first["blocks"][0]["text"],saved["resumeState"]["pending"]["text"])
        complete=assembler.process(second)
        self.assertEqual([1,2],complete["blocks"][0]["pages"])
        self.assertIn("requirement is one hundred",complete["blocks"][0]["text"])
        self.assertIsNone(complete["resumeState"]["pending"])
        resumed_settings=settings();resumed_settings.update(resume_pages=1,resume_batches=1,resume_state=json.loads(json.dumps(saved["resumeState"])))
        resumed=BoundaryAssembler(resumed_settings)
        self.assertEqual(complete,resumed.process(second))
        self.assertEqual("Chapter 1",resumed_settings["heading"])
        self.assertEqual([0,1],[f["batch_index"] for f in complete["blocks"][0]["source_fragments"]])

    def test_new_heading_flushes_old_tail_without_merging(self):
        assembler=BoundaryAssembler(settings())
        assembler.process(event(1,[block(1,"unfinished previous paragraph",bottom=True)]))
        result=assembler.process(event(2,[block(2,"Chapter 2",label="section_header",section="Chapter 2"),block(2,"Other content.",section="Chapter 2")]))
        self.assertEqual(3,len(result["blocks"]))
        self.assertEqual([1],result["blocks"][0]["pages"])

    def test_blank_page_does_not_allow_join_across_missing_page(self):
        assembler=BoundaryAssembler(settings())
        assembler.process(event(1,[block(1,"unfinished previous paragraph",bottom=True)],total=3))
        middle=assembler.process(event(2,[],total=3))
        last=assembler.process(event(3,[block(3,"continuation is not adjacent.")],total=3))
        texts=[b["text"] for batch in [middle,last] for b in batch["blocks"]]
        self.assertEqual(["unfinished previous paragraph","continuation is not adjacent."],texts)
        self.assertTrue(all(len(b["pages"])==1 for b in last["blocks"]))

    def test_final_batch_flushes_even_unfinished_tail(self):
        assembler=BoundaryAssembler(settings())
        final=assembler.process(event(1,[block(1,"unfinished but final",bottom=True)],total=1))
        self.assertEqual(1,len(final["blocks"]))
        self.assertIsNone(final["resumeState"]["pending"])
        self.assertEqual("complete",assembler.process({"type":"complete","totalPages":1})["type"])

    def test_resume_after_final_commit_needs_no_extra_batch(self):
        saved=BoundaryAssembler(settings()).process(event(1,[block(1,"Done.")],total=1))
        config=settings();config.update(resume_pages=1,resume_batches=1,resume_state=saved["resumeState"])
        self.assertEqual("complete",BoundaryAssembler(config).process({"type":"complete","totalPages":1})["type"])

    def test_old_checkpoints_keep_legacy_semantics(self):
        config=settings();config.update(resume_pages=1,resume_batches=1)
        assembler=BoundaryAssembler(config)
        second=event(2,[block(2,"continuation")])
        self.assertFalse(assembler.enabled)
        self.assertEqual(second,assembler.process(second))
        self.assertFalse(config["_continuity_active"])

    def test_disabling_new_jobs_cannot_discard_existing_pending_state(self):
        saved=BoundaryAssembler(settings()).process(event(1,[block(1,"unfinished requirement",bottom=True)]))["resumeState"]
        config=settings();config.update(resume_pages=1,resume_batches=1,resume_state=saved,continuity_enabled=False)
        resumed=BoundaryAssembler(config)
        result=resumed.process(event(2,[block(2,"continues here.")]))
        self.assertTrue(resumed.enabled)
        self.assertIn("unfinished requirement continues",result["blocks"][0]["text"])

    def test_malformed_explicit_state_cannot_fall_back_to_legacy(self):
        config=settings();config.update(resume_pages=1,resume_batches=1,resume_state={})
        with self.assertRaises(ValueError):BoundaryAssembler(config)

    def test_config_change_and_wrong_page_checkpoint_are_rejected(self):
        saved=BoundaryAssembler(settings()).process(event(1,[block(1,"unfinished",bottom=True)]))["resumeState"]
        config=settings();config.update(resume_pages=1,resume_batches=1,resume_state=saved,fast_batch_pages=2)
        with self.assertRaises(ValueError):BoundaryAssembler(config)
        config["fast_batch_pages"]=1;config["resume_pages"]=2
        with self.assertRaises(ValueError):BoundaryAssembler(config)

    def test_state_size_limit_submits_tail_instead_of_losing_it(self):
        item=block(1,"unfinished",bottom=True);item["extra"]="large metadata"*100
        with patch("boundary_stream.MAX_STATE_BYTES",500):
            result=BoundaryAssembler(settings()).process(event(1,[item]))
        self.assertEqual(1,len(result["blocks"]))
        self.assertIsNone(result["resumeState"]["pending"])
        self.assertTrue(result["continuity"]["carryLimitReached"])

    def test_transformed_docling_batches_build_canonical_view(self):
        config=settings();config["parse_mode"]="DOCLING"
        raw=event(1,[block(1,"unfinished paragraph",bottom=True)])
        raw["doclingDocument"]={"name":"raw"}
        with patch("chunking_document.attach_chunking_document") as attach:
            result=BoundaryAssembler(config).process(raw)
        attach.assert_called_once_with(result)
        self.assertEqual({"name":"raw"},result["doclingDocument"])

    def test_untouched_docling_batches_keep_original_structure(self):
        config=settings();config["parse_mode"]="DOCLING"
        raw=event(1,[block(1,"Complete sentence.")])
        raw["doclingDocument"]={"name":"raw"}
        with patch("chunking_document.attach_chunking_document") as attach:
            result=BoundaryAssembler(config).process(raw)
        attach.assert_not_called()
        self.assertNotIn("chunkingDocument",result)


if __name__=="__main__":unittest.main()
