"""Small actual Milvus experiment: do deletes restore BM25 corpus statistics?"""
import sys,json,uuid
sys.path.insert(0,'/retrieval')
import app as r
r.COLLECTION='parser_ab_idf_'+uuid.uuid4().hex[:8]
c=r.milvus();r.ensure_collection(2)
def row(i,text):
    return dict(chunk_id=str(i),section_id='s',content=text,search_text=text,doc_id='d',owner_id=99999983,index_version='x',sources='[]',dense=[1.,0.])
def score():
    return [(h['entity']['chunk_id'],h['distance']) for h in c.search(collection_name=r.COLLECTION,data=['revenue'],anns_field='sparse',
        filter='chunk_id in ["0","1"]',limit=2,output_fields=['chunk_id','content'],search_params={'metric_type':'BM25'},consistency_level='Strong')[0]]
try:
    c.upsert(collection_name=r.COLLECTION,data=[row(0,'revenue cash'),row(1,'income revenue revenue cash')]);a=score()
    c.upsert(collection_name=r.COLLECTION,data=[row(i,'revenue') for i in range(2,102)]);b=score()
    c.delete(collection_name=r.COLLECTION,filter='chunk_id not in ["0","1"]');d=score()
    print(json.dumps(dict(before=a,with_extra=b,after_delete=d)))
finally:c.drop_collection(r.COLLECTION)
