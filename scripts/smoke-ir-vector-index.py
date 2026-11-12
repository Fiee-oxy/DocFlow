"""Run INSIDE retrieval container. Uses a disposable Milvus collection only."""
import sys,json,uuid,time
sys.path.insert(0,'/retrieval')
import app as retrieval
started=time.perf_counter();retrieval.COLLECTION='parser_ir_smoke_'+uuid.uuid4().hex[:12]
doc=str(uuid.uuid4());version='ir-smoke-v1'
ir={'schemaVersion':'1.0','documentId':doc,'documentVersion':version,'parser':{'mode':'FAST','parserVersion':'smoke'},'blocks':[
    {'blockId':'b1','sourceRef':'page-1/block-1','type':'PARAGRAPH','text':'The graduation requirement is 160 credits.','headingPath':['Graduation requirements'],'pages':[1],'attributes':{}}]}
request=retrieval.ChunkRequest(document_id=doc,owner_id=99999991,index_version=version,batch_index=0,batch={'documentIR':ir})
try:
    first=retrieval.index(request)['chunks'];second=retrieval.index(request)['chunks']
    assert first and [c['chunk_id'] for c in first]==[c['chunk_id'] for c in second]
    found=retrieval.search(retrieval.SearchRequest(owner_id=99999991,scopes=[{'document_id':doc,'index_version':version}],question='graduation credits'))
    assert found['dense'] and found['sparse'],found
    wrong=retrieval.search(retrieval.SearchRequest(owner_id=99999992,scopes=[{'document_id':doc,'index_version':version}],question='graduation credits'))
    assert not wrong['dense'] and not wrong['sparse']
    assert first[0]['sources'][0]['ref']=='b0:page-1/block-1'
    print(json.dumps({'status':'PASSED','synthetic':True,'embedding':retrieval.EMBEDDING,'chunks':len(first),'dense':len(found['dense']),'sparse':len(found['sparse']),'repeat_ids_stable':True,'wrong_owner_empty':True,'elapsed_ms':(time.perf_counter()-started)*1000,'llmCalls':0}),flush=True)
finally:
    if retrieval.milvus().has_collection(retrieval.COLLECTION):retrieval.milvus().drop_collection(retrieval.COLLECTION)
