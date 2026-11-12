import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from copy import deepcopy
from query_expansion import QueryExpander, contains
import app as retrieval

DICTIONARY=Path(__file__).with_name("synonyms.json")


class QueryExpansionTests(unittest.TestCase):
    def expander(self, enabled=True, **options):
        return QueryExpander(DICTIONARY,enabled=enabled,**options)

    def test_disabled_is_identity_and_preview_never_loads_models(self):
        e=self.expander(False)
        self.assertEqual(e.expand("2024年的营收？")["expanded_query"],"2024年的营收？")
        with patch.object(retrieval,"query_expander",e),patch.object(retrieval,"encode") as encode:
            preview=retrieval.expand_query(retrieval.ExpansionRequest(question="营收多少"))
            self.assertEqual(preview["llmCalls"],0);encode.assert_not_called()

    def test_english_boundaries_and_chinese_alias(self):
        self.assertFalse(contains("staffing", "staff"))
        self.assertFalse(contains("employees", "employee"))
        self.assertTrue(contains("STAFF count", "staff"))
        result=self.expander().expand("2024年茅台营收多少？")
        self.assertEqual(result["terms"],["营业收入"])
        self.assertTrue(result["expanded_query"].startswith(result["original_query"]))
        self.assertIn("2024",result["expanded_query"])

    def test_no_expansion_without_known_concept_and_bounded_deterministic_plan(self):
        e=self.expander(max_terms=2)
        self.assertEqual(e.expand("HTTP Retry-After 2024")["terms"],[])
        q="staff capital spending"
        self.assertEqual(len(e.expand(q)["terms"]),2)
        self.assertEqual(e.expand(q),e.expand(q))
        self.assertEqual(e.sentence_bonus("staff","employee employees staff"),.25)

    def test_reject_numeric_answer_or_overlapping_dictionary(self):
        for groups in ([["employees","208000 employees"]],[["staff","employee"],["staff","worker"]]):
            with tempfile.TemporaryDirectory() as directory:
                p=Path(directory)/"bad.json";p.write_text(json.dumps({"groups":groups}),encoding="utf8")
                with self.assertRaises(ValueError):QueryExpander(p)

    def test_weighted_merge_admits_bounded_novel_alias_hits_and_keeps_original_leaders(self):
        original=[{"chunk_id":str(i)} for i in range(30)]
        extra=[{"chunk_id":"new"+str(i)} for i in range(5)]
        merged=retrieval.merge_lexical_routes([original,original,extra],30,[1,1,.25],reserve_last=2)
        ids={h["chunk_id"] for h in merged}
        self.assertEqual(len(ids),30)
        self.assertTrue({"0","1","2","3","4","new0","new1"}<=ids)
        self.assertFalse("new2" in ids)
        self.assertLess(next(h["lexical_rrf_score"] for h in merged if h["chunk_id"]=="new0"),
                        next(h["lexical_rrf_score"] for h in merged if h["chunk_id"]=="0"))

    @staticmethod
    def hit(key,content):
        return {"distance":1,"entity":{"chunk_id":key,"content":content,"search_text":content,
            "doc_id":"doc","index_version":"v1","section_id":"s","sources":"[]"}}

    def test_search_adds_one_scoped_route_without_changing_dense_question(self):
        client=Mock();client.has_collection.return_value=True
        base=[self.hit(str(i),"original content") for i in range(30)]
        client.search.side_effect=[[deepcopy(base)],[deepcopy(base)],[deepcopy(base)],[[self.hit("new","employees count"),self.hit("noise","unrelated content")]]]
        q="How many staff were there in 2024?"
        request=retrieval.SearchRequest(owner_id=7,scopes=[{"document_id":"doc","index_version":"v1"}],question=q,dense_top_k=30,sparse_top_k=30)
        with patch.object(retrieval,"query_expander",self.expander()),patch.object(retrieval,"milvus",return_value=client),patch.object(retrieval,"encode",return_value=[[1.0]]) as encode:
            result=retrieval.search(request)
        encode.assert_called_once_with([q],query=True)
        self.assertEqual(client.search.call_count,4)
        self.assertIn("new",{h["chunk_id"] for h in result["sparse"]})
        self.assertNotIn("noise",{h["chunk_id"] for h in result["sparse"]})
        filters={c.kwargs["filter"] for c in client.search.call_args_list}
        self.assertEqual(len(filters),1);self.assertIn("owner_id in [7]",filters.pop())
        self.assertTrue(client.search.call_args.kwargs["data"][0].startswith(q))

    def test_alias_changes_temporary_sentence_choice_only(self):
        class Tokenizer:
            def __call__(self,text,**kwargs):return {"input_ids":list(range(len(text.split())))}
        text="A very long heading that cannot fit\nplain text\nother words\nstaff details available"
        with patch.object(retrieval,"query_expander",self.expander()):
            selected=retrieval.truncate_for_rerank("employees",text,Tokenizer(),4)
        self.assertEqual(selected,"staff details available")
        with patch.object(retrieval,"query_expander",self.expander(False)):
            baseline=retrieval.truncate_for_rerank("employees",text,Tokenizer(),4)
        self.assertNotIn("staff",baseline)


if __name__=="__main__":unittest.main()
