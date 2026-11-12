import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import app
from chunk_strategies import boundary
class CharacterTokenizer:
 def encode(self,text,add_special_tokens=False): return list(range(len(text)+(2 if add_special_tokens else 0)))
 def __call__(self,text,**kwargs):return {'offset_mapping':[(i,i+1) for i in range(len(text))]}
class ChunkStrategyTest(unittest.TestCase):
 def test_exact_preservation_and_bound(self):
  text='First sentence. Another paragraph!\n\n第二段原文。列表：\n1、测试内容\n2、下一条。' * 12
  pieces=list(boundary(text,'Title',CharacterTokenizer(),50,80))
  self.assertEqual(text,''.join(pieces));self.assertTrue(all(len(p)<=50 for p in pieces))
 def test_prefers_sentence_end(self):
  text='a'*25+'. '+'b'*40
  self.assertEqual('a'*25+'. ',list(boundary(text,'T',CharacterTokenizer(),40,80))[0])
 def test_idempotent_and_configuration_changes_identity(self):
  with patch.object(app,'embedding_models',return_value=(CharacterTokenizer(),None)):
   req=app.ChunkRequest(document_id='doc',owner_id=1,index_version='v',batch_index=0,batch={'blocks':[{'text':'abc。'*40,'section':'T','pages':[2],'ref':'r'}]},strategy='boundary',target_tokens=40,max_tokens=80,merge_peers=False)
   first=app.chunks_for(req);self.assertEqual(first,app.chunks_for(req));self.assertEqual([2],first[0]['sources'][0]['pages'])
   other=app.chunks_for(req.model_copy(update={'target_tokens':41}))
   self.assertNotEqual(first[0]['chunk_id'],other[0]['chunk_id'])
 def test_revision_mismatch_stops_resume(self):
  request=app.ChunkRequest(document_id='d',owner_id=1,index_version='v',batch_index=0,batch={},strategy='boundary',strategy_revision='boundary-v0')
  with self.assertRaises(app.HTTPException) as error:app.chunks_for(request)
  self.assertEqual(409,error.exception.status_code)
 def test_preview_does_not_index(self):
  with patch.object(app,'embedding_models',return_value=(CharacterTokenizer(),None)),patch.object(app,'milvus',side_effect=AssertionError('must not write')),patch.dict('os.environ',{'RETRIEVAL_API_KEY':''}):
   client=TestClient(app.app)
   body=dict(document_id='d',owner_id=1,index_version='v',batch_index=0,batch={'blocks':[{'text':'abc'}]},strategy='boundary')
   response=client.post('/chunk/preview',json=body)
   self.assertEqual(200,response.status_code);self.assertEqual(0,response.json()['writes'])
   body['strategy']='invalid';self.assertEqual(422,client.post('/chunk/preview',json=body).status_code)
if __name__=='__main__':unittest.main()
