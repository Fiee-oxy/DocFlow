"""Re-index frozen parser batches in a NEW collection per run, eliminating BM25 IDF carryover."""
import sys,json,time,uuid,hashlib
from pathlib import Path
sys.path.insert(0,'/tmp')
import importlib.util
spec=importlib.util.spec_from_file_location('comparison','/tmp/compare-parser-paths.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
r=b.r;root=b.OUT
rows=json.loads((root/'results.json').read_text())
original=root/'results-shared-collection-discarded.json'
if not original.exists():b.save(original,rows)
cleanups=[]
r.encode(['benchmark warmup']);r.rerank_scores('benchmark',['benchmark evidence'])
for row in rows:
    d=next(d for d in b.M['documents'] if d['id']==row['document'])
    folder=root/d['id']/row['mode'];collection='parser_ab_clean_'+uuid.uuid4().hex[:16]
    r.COLLECTION=collection
    try:
        assert not r.milvus().has_collection(collection)
        oldqueries=[json.loads((folder/(c['case_id']+'.json')).read_text()) for c in d['cases']]
        doc=next((h['doc_id'] for q in oldqueries for h in q['search']['dense']+q['search']['sparse']),str(uuid.uuid4()))
        chunks=[];index_ms=0
        batches=[json.loads(l) for l in (folder/'events.jsonl').read_text().splitlines() if l.strip()]
        for batch_index,event in enumerate(e for e in batches if e['type']=='batch'):
            t=time.perf_counter();indexed=r.index(r.ChunkRequest(document_id=doc,owner_id=b.OWNER,index_version='ab-v1',batch_index=batch_index,batch=event))
            index_ms+=(time.perf_counter()-t)*1000;chunks.extend(indexed['chunks'])
        frozen=json.loads((folder/'chunks.json').read_text())
        assert [c['chunk_id'] for c in chunks]==[c['chunk_id'] for c in frozen], 'Frozen chunks changed'
        queries=[]
        for case in d['cases']:
            original_file=folder/(case['case_id']+'.json')
            archive=folder/(case['case_id']+'-shared-collection-discarded.json')
            if not archive.exists():archive.write_bytes(original_file.read_bytes())
            old=next(q for q in row['queries'] if q['case_id']==case['case_id'])
            t=time.perf_counter();found=r.search(r.SearchRequest(owner_id=b.OWNER,scopes=[{'document_id':doc,'index_version':'ab-v1'}],question=case['question'],dense_top_k=30,sparse_top_k=30));search_ms=(time.perf_counter()-t)*1000
            t=time.perf_counter();fused=b.rrf(found);rrf_ms=(time.perf_counter()-t)*1000
            t=time.perf_counter();ranked=r.rerank(r.RerankRequest(question=case['question'],candidates=fused,title_boost=True))['candidates'];rerank_ms=(time.perf_counter()-t)*1000
            union={c['chunk_id']:c for c in found['dense']+found['sparse']}
            result=old|{'candidate':b.coverage(b.sources(union.values()),case),'rrf':b.coverage(b.sources(fused),case),
                'rerank_top12':b.coverage(b.sources(ranked[:12]),case),'search_ms':search_ms,'rrf_ms':rrf_ms,'rerank_ms':rerank_ms,
                'total_query_ms':search_ms+rrf_ms+rerank_ms,'retrieval_collection_basis':'FRESH_COLLECTION_PER_RUN'}
            queries.append(result);b.save(original_file,{'result':result,'search':found,'rrf':fused,'ranked':ranked})
        row.update(queries=queries,retrieval_collection_basis='FRESH_COLLECTION_PER_RUN',isolated_reindex_ms=index_ms)
        b.save(folder/'result.json',row);b.save(root/'results.json',rows)
        print(json.dumps({'document':d['id'],'mode':row['mode'],'isolated_index_ms':index_ms,'complete_evidence':sum(q['rerank_top12']['complete'] for q in queries)},ensure_ascii=False),flush=True)
    finally:
        if r.milvus().has_collection(collection):r.milvus().drop_collection(collection)
        cleanups.append({'collection':collection,'exists':r.milvus().has_collection(collection),'document':d['id'],'mode':row['mode']})
        b.save(root/'isolated-retrieval-cleanup.json',cleanups)
b.save(root/'isolated-retrieval-summary.json',{'runs':len(rows),'queries':sum(len(r['queries']) for r in rows),'llmCalls':0,'freshCollectionPerRun':True,'allCollectionsRemoved':all(not c['exists'] for c in cleanups)})
