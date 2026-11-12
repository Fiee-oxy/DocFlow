import unittest
from unittest.mock import patch,Mock
import app as r


class RouteEndpointTests(unittest.TestCase):
    def hit(self,id,text="content",score=1):
        return dict(chunk_id=id,content=text,score=score,sources=[])

    def request(self,q):return r.RoutedRequest(owner_id=7,scopes=[dict(document_id="d",index_version="v")],question=q)

    def test_fallback_only_fetches_missing_route(self):
        sparse=self.hit("s");dense=self.hit("d")
        with patch.object(r,"search",side_effect=[dict(dense=[],sparse=[]),dict(dense=[dense],sparse=[])]) as search,patch.object(r,"rerank",side_effect=lambda request:dict(candidates=request.candidates)):
            result=r.retrieve_routed(self.request("find exact 0x154"))
        self.assertEqual(["BM25","DENSE"],[c.args[0].retrieval_mode for c in search.call_args_list])
        self.assertTrue(result["routing"]["fallback"])
        self.assertEqual({"d"},{h["chunk_id"] for h in result["selected"]})

    def test_nonempty_literal_keeps_rerank_without_fallback(self):
        with patch.object(r,"search",return_value=dict(dense=[],sparse=[self.hit("a","unique phrase",20)])) as search,patch.object(r,"rerank",side_effect=lambda request:dict(candidates=request.candidates)) as rerank:
            result=r.retrieve_routed(self.request('Find "unique phrase"'))
        search.assert_called_once();rerank.assert_called_once()
        self.assertFalse(result["routing"]["rerankSkipped"])
        self.assertEqual(7,search.call_args.args[0].owner_id)

    def test_complex_query_keeps_hybrid_and_rerank(self):
        with patch.object(r,"search",return_value=dict(dense=[self.hit("a")],sparse=[])) as search,patch.object(r,"rerank",return_value=dict(candidates=[])) as rerank:
            result=r.retrieve_routed(self.request("List all requirements"))
        self.assertEqual("HYBRID",search.call_args.args[0].retrieval_mode)
        rerank.assert_called_once();self.assertFalse(result["routing"]["fallback"])

    def test_bm25_search_does_not_load_embedding(self):
        client=Mock();client.has_collection.return_value=True;client.search.return_value=[[]]
        with patch.object(r,"milvus",return_value=client),patch.object(r,"encode") as encode:
            r.search(r.SearchRequest(owner_id=7,scopes=[dict(document_id="d",index_version="v")],question="revenue",retrieval_mode="BM25"))
        encode.assert_not_called()

    def test_permission_error_never_triggers_fallback(self):
        with patch.object(r,"search",side_effect=r.HTTPException(403,"denied")) as search:
            with self.assertRaises(r.HTTPException):r.retrieve_routed(self.request('find exact "secret"'))
        search.assert_called_once()

    def test_unready_and_unsupported_page_scope_fail_closed(self):
        with patch.object(r,"search") as search:
            with self.assertRaises(r.HTTPException):r.retrieve_routed(self.request("query").model_copy(update={"index_ready":False}))
            with self.assertRaises(r.HTTPException):r.retrieve_routed(self.request("query").model_copy(update={"page_range":[1,2]}))
        search.assert_not_called()

    def test_single_route_does_not_apply_fusion_truncation(self):
        hits=[self.hit(str(i),'needle') for i in range(30)]
        with patch.object(r,"search",return_value=dict(dense=[],sparse=hits)),patch.object(r,"rerank",side_effect=lambda request:dict(candidates=request.candidates)):
            result=r.retrieve_routed(self.request('find exact "needle"'))
        self.assertEqual(30,len(result["fused"]))

    def test_baseline_does_not_depend_on_routing_dictionary(self):
        with patch("retrieval_router.plan",side_effect=AssertionError("router should be disabled")),patch.object(r,"search",return_value=dict(dense=[],sparse=[])),patch.object(r,"rerank") as rerank:
            result=r.retrieve_routed(self.request("query").model_copy(update={"execution_mode":"BASELINE"}))
        rerank.assert_not_called();self.assertEqual([],result["selected"])
        with patch("retrieval_router.rules",side_effect=AssertionError("dictionary should not be loaded")):
            self.assertNotIn("routing_policy",r.expand_query(r.ExpansionRequest(question="query")))

    def test_dense_empty_only_fetches_bm25_once(self):
        with patch.object(r,"search",side_effect=[dict(dense=[],sparse=[]),dict(dense=[],sparse=[self.hit("s")])]) as search,patch.object(r,"rerank",side_effect=lambda request:dict(candidates=request.candidates)):
            result=r.retrieve_routed(self.request("find semantically similar text"))
        self.assertEqual(["DENSE","BM25"],[c.args[0].retrieval_mode for c in search.call_args_list]);self.assertEqual(1,result["routing"]["fallbackAttempts"])

    def test_nonempty_low_confidence_adds_only_missing_route_then_reranks_once(self):
        request=self.request('find exact "target"').model_copy(update={'section_ids':['section-1']})
        sparse=self.hit('s','unrelated',999);dense=self.hit('d','target',.8)
        with patch.object(r,'search',side_effect=[dict(dense=[],sparse=[sparse]),dict(dense=[dense],sparse=[])]) as search,patch.object(r,'rerank',side_effect=lambda req:dict(candidates=req.candidates)) as rerank:
            result=r.retrieve_routed(request)
        self.assertEqual(['BM25','DENSE'],[c.args[0].retrieval_mode for c in search.call_args_list])
        self.assertTrue(all(c.args[0].owner_id==7 and c.args[0].scopes==request.scopes and c.args[0].section_ids==['section-1'] for c in search.call_args_list))
        self.assertEqual({'s','d'},{h['chunk_id'] for h in result['fused']});rerank.assert_called_once()
        self.assertEqual('LOW_CONFIDENCE',result['routing']['fallbackReason']);self.assertEqual('HYBRID',result['routing']['finalRoute'])

    def test_dense_low_score_and_disabled_low_confidence_switch(self):
        request=self.request('find semantically similar text')
        weak=dict(dense=[self.hit('d','synonym',.1)],sparse=[])
        with patch.object(r,'search',side_effect=[weak,dict(dense=[],sparse=[self.hit('s')])]) as search,patch.object(r,'rerank',side_effect=lambda req:dict(candidates=req.candidates)):
            result=r.retrieve_routed(request)
        self.assertEqual(['DENSE','BM25'],[c.args[0].retrieval_mode for c in search.call_args_list]);self.assertEqual(1,result['routing']['fallbackAttempts'])
        with patch.object(r,'search',return_value=weak) as search,patch.object(r,'rerank',side_effect=lambda req:dict(candidates=req.candidates)):
            result=r.retrieve_routed(request.model_copy(update={'fallback_on_low_confidence':False}))
        search.assert_called_once();self.assertFalse(result['routing']['fallback'])

    def test_quality_thresholds_validated_before_execution(self):
        for key,value in [('dense_min_score',float('nan')),('bm25_min_coverage',1.1),('quality_top_k',0)]:
            with self.assertRaises(ValueError):r.RoutedRequest(owner_id=7,scopes=[],question='q',**{key:value})


if __name__=="__main__":unittest.main()
