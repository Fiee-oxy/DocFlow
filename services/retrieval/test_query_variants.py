import unittest
from unittest.mock import patch
import app as r

class VariantTests(unittest.TestCase):
    def test_original_and_variants_share_scope_and_rerank_only_once(self):
        calls=[]
        def search(req):
            calls.append(req)
            hit=dict(chunk_id='original' if len(calls)==1 else 'alias',doc_id='d',index_version='v',content='evidence',sources=[])
            return dict(dense=[hit],sparse=[],timingMs={})
        request=r.RoutedRequest(question='original query',owner_id=7,scopes=[dict(document_id='d',index_version='v')],
            section_ids=['s'],query_variants=['alternative query'],execution_mode='BASELINE')
        with patch.object(r,'search',side_effect=search),patch.object(r,'rerank',side_effect=lambda req:dict(candidates=req.candidates)) as rank:
            response=r.retrieve_routed(request)
        self.assertEqual(2,len(calls));self.assertEqual(1,rank.call_count)
        self.assertTrue(all(x.owner_id==7 and x.scopes==request.scopes and x.section_ids==['s'] for x in calls))
        self.assertEqual({'original','alias'},{h['chunk_id'] for h in response['fused']})
        self.assertTrue(response['routing']['originalQueryRetained'])
    def test_invalid_variant_cannot_trigger_search(self):
        with patch.object(r,'search') as search:
            with self.assertRaises(r.HTTPException):r.retrieve_routed(r.RoutedRequest(question='q',owner_id=7,scopes=[],query_variants=[' ']))
        search.assert_not_called()
if __name__=='__main__':unittest.main()
