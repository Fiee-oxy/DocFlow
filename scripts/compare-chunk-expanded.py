"""Frozen identical FAST outputs, separate fresh collections, no chat LLM."""
import importlib.util,json,time,uuid,sys,traceback
from pathlib import Path
sys.path.insert(0,'/retrieval')
spec=importlib.util.spec_from_file_location('comparison','/tmp/compare-parser-paths.py');b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
from evidence_metrics import coverage as evidence_coverage,source_order
r=b.r
b.M=json.loads(Path('/tmp/chunk-expanded-manifest.json').read_text())
out=Path('/tmp/chunk-expanded-results');out.mkdir(exist_ok=True)
rows=[]
r.encode(['warmup']);r.rerank_scores('warmup',['warmup evidence'])
for d in b.M['documents']:
 for strategy in ['legacy','boundary']:
  collection='chunk_ab_'+uuid.uuid4().hex[:16];r.COLLECTION=collection
  folder=out/d['id']/strategy;folder.mkdir(parents=True,exist_ok=True)
  row={'document':d['id'],'language':d['language'],'pages':d['pages'],'strategy':strategy,'group':d['group'],'input_kind':d['input_kind'],'llmCalls':0,'queries':[]}
  try:
   events=[json.loads(l) for l in (Path('/tmp/chunk-expanded-input')/d['id']/'events.jsonl').read_text().splitlines() if l.strip()]
   sweep=[]
   for target in [256,350,450]:
    t=time.perf_counter();samples=[]
    for n,event in enumerate(e for e in events if e['type']=='batch'):
     samples.extend(r.chunks_for(r.ChunkRequest(document_id=d['id'],owner_id=b.OWNER,index_version='sweep-v1',batch_index=n,batch=event,strategy=strategy,target_tokens=target)))
    originals=[c for c in d['cases'] if c['variant']=='ORIGINAL']
    sweep.append({'target_tokens':target,'chunks':len(samples),'elapsed_ms':(time.perf_counter()-t)*1000,'questions':len(originals),'complete':sum(evidence_coverage(b.sources(samples),c,source_order(samples))['complete'] for c in originals),'max_embedding_tokens':max((c['token_count'] for c in samples),default=0)})
   row['budget_sweep']=sweep
   chunks=[];blocks=[];start=time.perf_counter();chunk_ms=0
   for n,event in enumerate(e for e in events if e['type']=='batch'):
    request=r.ChunkRequest(document_id=d['id'],owner_id=b.OWNER,index_version='chunk-ab-v1',batch_index=n,batch=event,strategy=strategy)
    t=time.perf_counter();preview=r.chunks_for(request);chunk_ms+=(time.perf_counter()-t)*1000
    indexed=r.index(request)['chunks'];assert preview==indexed
    chunks.extend(indexed);blocks.extend(event.get('blocks',[]))
   row.update(index_ms=(time.perf_counter()-start)*1000-chunk_ms,chunk_ms=chunk_ms,chunks=len(chunks))
   b.save(folder/'chunks.json',chunks)
   order=source_order(chunks)
   for case in d['cases']:
    t=time.perf_counter();found=r.search(r.SearchRequest(owner_id=b.OWNER,scopes=[{'document_id':d['id'],'index_version':'chunk-ab-v1'}],question=case['question'],dense_top_k=30,sparse_top_k=30));search_ms=(time.perf_counter()-t)*1000
    t=time.perf_counter();fused=b.rrf(found);rrf_ms=(time.perf_counter()-t)*1000
    t=time.perf_counter();ranked=r.rerank(r.RerankRequest(question=case['question'],candidates=fused,title_boost=True))['candidates'];rerank_ms=(time.perf_counter()-t)*1000
    union={c['chunk_id']:c for c in found['dense']+found['sparse']}
    q={'case_id':case['case_id'],'question':case['question'],'parse':b.coverage(blocks,case),'chunk':evidence_coverage(b.sources(chunks),case,order),'candidate':evidence_coverage(b.sources(union.values()),case,order),'rrf':evidence_coverage(b.sources(fused),case,order),'rerank_top12':evidence_coverage(b.sources(ranked[:12]),case,order),'search_ms':search_ms,'rrf_ms':rrf_ms,'rerank_ms':rerank_ms,'total_ms':search_ms+rrf_ms+rerank_ms}
    q.update(group=case['group'],variant=case['variant'],base_case_id=case['base_case_id'])
    q['coverage_at_k']={str(k):evidence_coverage(b.sources(ranked[:k]),case,order) for k in [1,3,5,8,12,28]}
    q['first_complete_rank']=next((k for k in range(1,len(ranked)+1) if evidence_coverage(b.sources(ranked[:k]),case,order)['complete']),None)
    row['queries'].append(q);b.save(folder/(case['case_id']+'.json'),{'result':q,'search':found,'rrf':fused,'ranked':ranked})
   row['status']='COMPLETE'
  except Exception:
   row.update(status='FAILED',error=traceback.format_exc())
  finally:
   if r.milvus().has_collection(collection):r.milvus().drop_collection(collection)
   row['collection_removed']=not r.milvus().has_collection(collection)
   rows.append(row);b.save(out/'results.json',rows)
   print(json.dumps({k:v for k,v in row.items() if k!='queries'},ensure_ascii=False),flush=True)
