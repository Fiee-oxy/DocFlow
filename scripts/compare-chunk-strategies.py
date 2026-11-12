"""Frozen identical FAST outputs, separate fresh collections, no chat LLM."""
import importlib.util,json,time,uuid,sys,traceback
from pathlib import Path
sys.path.insert(0,'/retrieval')
spec=importlib.util.spec_from_file_location('comparison','/tmp/compare-parser-paths.py');b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
r=b.r
out=Path('/tmp/chunk-comparison-results');out.mkdir(exist_ok=True)
rows=[]
r.encode(['warmup']);r.rerank_scores('warmup',['warmup evidence'])
for d in b.M['documents']:
 for strategy in ['legacy','boundary']:
  collection='chunk_ab_'+uuid.uuid4().hex[:16];r.COLLECTION=collection
  folder=out/d['id']/strategy;folder.mkdir(parents=True,exist_ok=True)
  row={'document':d['id'],'language':d['language'],'pages':d['pages'],'strategy':strategy,'llmCalls':0,'queries':[]}
  try:
   events=[json.loads(l) for l in (Path('/tmp/chunk-comparison-input')/d['id']/'legacy_fast'/'events.jsonl').read_text().splitlines() if l.strip()]
   chunks=[];blocks=[];start=time.perf_counter();chunk_ms=0
   for n,event in enumerate(e for e in events if e['type']=='batch'):
    request=r.ChunkRequest(document_id=d['id'],owner_id=b.OWNER,index_version='chunk-ab-v1',batch_index=n,batch=event,strategy=strategy)
    t=time.perf_counter();preview=r.chunks_for(request);chunk_ms+=(time.perf_counter()-t)*1000
    indexed=r.index(request)['chunks'];assert preview==indexed
    chunks.extend(indexed);blocks.extend(event.get('blocks',[]))
   row.update(index_ms=(time.perf_counter()-start)*1000-chunk_ms,chunk_ms=chunk_ms,chunks=len(chunks))
   b.save(folder/'chunks.json',chunks)
   for case in d['cases']:
    t=time.perf_counter();found=r.search(r.SearchRequest(owner_id=b.OWNER,scopes=[{'document_id':d['id'],'index_version':'chunk-ab-v1'}],question=case['question'],dense_top_k=30,sparse_top_k=30));search_ms=(time.perf_counter()-t)*1000
    t=time.perf_counter();fused=b.rrf(found);rrf_ms=(time.perf_counter()-t)*1000
    t=time.perf_counter();ranked=r.rerank(r.RerankRequest(question=case['question'],candidates=fused,title_boost=True))['candidates'];rerank_ms=(time.perf_counter()-t)*1000
    union={c['chunk_id']:c for c in found['dense']+found['sparse']}
    q={'case_id':case['case_id'],'question':case['question'],'parse':b.coverage(blocks,case),'chunk':b.coverage(b.sources(chunks),case),'candidate':b.coverage(b.sources(union.values()),case),'rrf':b.coverage(b.sources(fused),case),'rerank_top12':b.coverage(b.sources(ranked[:12]),case),'search_ms':search_ms,'rrf_ms':rrf_ms,'rerank_ms':rerank_ms,'total_ms':search_ms+rrf_ms+rerank_ms}
    row['queries'].append(q);b.save(folder/(case['case_id']+'.json'),{'result':q,'search':found,'rrf':fused,'ranked':ranked})
   row['status']='COMPLETE'
  except Exception:
   row.update(status='FAILED',error=traceback.format_exc())
  finally:
   if r.milvus().has_collection(collection):r.milvus().drop_collection(collection)
   row['collection_removed']=not r.milvus().has_collection(collection)
   rows.append(row);b.save(out/'results.json',rows)
   print(json.dumps({k:v for k,v in row.items() if k!='queries'},ensure_ascii=False),flush=True)
