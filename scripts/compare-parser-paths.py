"""Run inside retrieval container: actual parser -> incremental BGE/Milvus -> RRF/rerank.

Dedicated disposable collection; persists every run before continuing; no chat LLM.
Uses production Python index/search/rerank and a transparent RRF reference (not Java
chapter expansion/context planning). Timing excludes upload/MQ/SQL; those boundaries
are explicitly recorded rather than mislabeled as application end-to-end latency.
"""
import json,sys,time,uuid,unicodedata,re,traceback
from pathlib import Path
import requests
sys.path.insert(0,'/retrieval')
import app as r

OUT=Path('/tmp/parser-ab-results');OUT.mkdir(exist_ok=True)
M=json.loads(Path('/tmp/parser-ab-manifest.json').read_text())
r.COLLECTION=M['collection']
OWNER=99999983

def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def norm(text):
    return re.sub(r'[^\w\u3400-\u9fff]','',unicodedata.normalize('NFKC',text).casefold())
def coverage(items,case,text_key='text'):
    details=[]
    for unit in case['units']:
        scoped=[i for i in items if set(i.get('pages',[])) & set(unit['pages'])]
        text=norm('\n'.join(i.get(text_key,'') for i in scoped))
        details.append({'pages':unit['pages'],'atoms':[{'text':a,'found':norm(a) in text} for a in unit['atoms']]})
    atoms=[a['found'] for u in details for a in u['atoms']]
    return {'complete':bool(atoms) and all(atoms),'atoms_found':sum(atoms),'atoms_total':len(atoms),'units':details}
def sources(chunks):return [s for c in chunks for s in c.get('sources',[])]
def rrf(search):
    scores={};hits={}
    for route in [search['dense'],search['sparse']]:
        for rank,c in enumerate(route,1):
            key=c['chunk_id'];hits.setdefault(key,c);scores[key]=scores.get(key,0)+1/(60+rank)
    order=sorted(hits,key=lambda k:(-scores[k],k))
    protected={c['chunk_id'] for route in [search['dense'],search['sparse']] for c in route[:5]}
    for key in order:
        if len(protected)>=28:break
        protected.add(key)
    return [hits[k]|{'rrf_score':scores[k]} for k in order if k in protected]

def run(d,mode):
    # A scoped delete does not reset BM25 IDF immediately. Never reuse a collection
    # across comparison paths, even when previous entities are no longer visible.
    collection=M['collection']+'_run_'+uuid.uuid4().hex[:10]
    r.COLLECTION=collection
    assert not r.milvus().has_collection(collection)
    doc=str(uuid.uuid4());folder=OUT/d['id']/mode;folder.mkdir(parents=True,exist_ok=True)
    start=time.perf_counter();row={'document':d['id'],'mode':mode,'language':d['language'],'pages':d['pages'],
        'bytes':d['bytes'],'sha256':d['sha256'],'status':'RUNNING','first_parse_ms':None,'first_searchable_ms':None,
        'first_nonempty_searchable_ms':None,'parse_compute_observed_ms':0,'index_ms':0,'batch_count':0,'chunks':0,
        'timing_basis':'internal parser transport + synchronous incremental index; excludes upload/MQ/SQL',
        'llmCalls':0,'queries':[]}
    blocks=[];chunks=[];events=[]
    try:
        response=requests.post(M['parser_url']+'/run/'+d['id']+'/'+mode,stream=True,timeout=(10,900));response.raise_for_status()
        # Indexing is synchronous consumer backpressure; record it separately.
        with (folder/'events.jsonl').open('w',encoding='utf-8') as log:
            for line in response.iter_lines(chunk_size=8192):
                if not line:continue
                event=json.loads(line);log.write(json.dumps(event,ensure_ascii=False)+'\n');log.flush()
                if event['type']=='batch':
                    observed=(time.perf_counter()-start)*1000
                    if row['first_parse_ms'] is None:row['first_parse_ms']=observed
                    blocks.extend(event.get('blocks',[]));t=time.perf_counter()
                    indexed=r.index(r.ChunkRequest(document_id=doc,owner_id=OWNER,index_version='ab-v1',batch_index=row['batch_count'],batch=event))
                    row['index_ms']+=(time.perf_counter()-t)*1000
                    fresh=indexed['chunks'];chunks.extend(fresh);row['batch_count']+=1;row['chunks']=len(chunks)
                    if fresh and row['first_searchable_ms'] is None:
                        probe=r.search(r.SearchRequest(owner_id=OWNER,scopes=[{'document_id':doc,'index_version':'ab-v1'}],question=d['cases'][0]['question'],dense_top_k=30,sparse_top_k=30))
                        if probe['dense'] or probe['sparse']:
                            row['first_searchable_ms']=(time.perf_counter()-start)*1000
                            if any(c['content'].strip() for c in probe['dense']+probe['sparse']):row['first_nonempty_searchable_ms']=row['first_searchable_ms']
                    save(folder/'progress.json',row)
                elif event['type']=='complete':row['status']='COMPLETE'
                elif event['type']=='error':row['status']='FAILED';row['error']=event;break
        row['pipeline_total_ms']=(time.perf_counter()-start)*1000
        row['parser_wall_less_index_ms']=row['pipeline_total_ms']-row['index_ms']
        if row['status']=='RUNNING':row['status']='INCOMPLETE_STREAM'
        save(folder/'blocks.json',blocks);save(folder/'chunks.json',chunks)
        for case in d['cases']:
            t=time.perf_counter();found=r.search(r.SearchRequest(owner_id=OWNER,scopes=[{'document_id':doc,'index_version':'ab-v1'}],question=case['question'],dense_top_k=30,sparse_top_k=30));search_ms=(time.perf_counter()-t)*1000
            t=time.perf_counter();fused=rrf(found);rrf_ms=(time.perf_counter()-t)*1000
            t=time.perf_counter();ranked=r.rerank(r.RerankRequest(question=case['question'],candidates=fused,title_boost=True))['candidates'];rerank_ms=(time.perf_counter()-t)*1000
            union={c['chunk_id']:c for c in found['dense']+found['sparse']}
            result={'case_id':case['case_id'],'question':case['question'],'parse':coverage(blocks,case),
                    'chunk':coverage(sources(chunks),case),'candidate':coverage(sources(union.values()),case),
                    'rrf':coverage(sources(fused),case),'rerank_top12':coverage(sources(ranked[:12]),case),
                    'search_ms':search_ms,'rrf_ms':rrf_ms,'rerank_ms':rerank_ms,
                    'total_query_ms':(time.perf_counter()-t)*1000+search_ms+rrf_ms}
            row['queries'].append(result);save(folder/(case['case_id']+'.json'),{'result':result,'search':found,'rrf':fused,'ranked':ranked})
    except Exception as e:
        row.update(status='FAILED',exception=str(e),traceback=traceback.format_exc(),pipeline_total_ms=(time.perf_counter()-start)*1000)
    finally:
        if r.milvus().has_collection(collection):r.milvus().drop_collection(collection)
        row['temporary_collection_deleted']=not r.milvus().has_collection(collection)
        row['retrieval_collection_basis']='FRESH_COLLECTION_PER_RUN'
        save(folder/'result.json',row)
    print(json.dumps({k:v for k,v in row.items() if k!='queries'},ensure_ascii=False),flush=True)
    return row

def rerank_replay():
    rows=json.loads((OUT/'results.json').read_text())
    for row in rows:
        d=next(d for d in M['documents'] if d['id']==row['document'])
        for result in row['queries']:
            case=next(c for c in d['cases'] if c['case_id']==result['case_id'])
            path=OUT/d['id']/row['mode']/(case['case_id']+'.json');raw=json.loads(path.read_text())
            t=time.perf_counter();fused=rrf(raw['search']);rrf_ms=(time.perf_counter()-t)*1000
            t=time.perf_counter();ranked=r.rerank(r.RerankRequest(question=case['question'],candidates=fused,title_boost=True))['candidates'];rerank_ms=(time.perf_counter()-t)*1000
            result.update(rrf=coverage(sources(fused),case),rerank_top12=coverage(sources(ranked[:12]),case),rrf_ms=rrf_ms,rerank_ms=rerank_ms,
                          total_query_ms=result['search_ms']+rrf_ms+rerank_ms,fusion_basis='PRODUCTION_RRF_TOP5_ROUTE_RESERVE_REFERENCE',rerank_replay=True)
            save(path,{'result':result,'search':raw['search'],'rrf':fused,'ranked':ranked})
        save(OUT/d['id']/row['mode']/'result.json',row);save(OUT/'results.json',rows)
        print('REFRESHED',row['document'],row['mode'],flush=True)

if __name__=='__main__' and '--rerank-replay' in sys.argv:
    rerank_replay()
elif __name__=='__main__':
    rows=[]
    try:
        t=time.perf_counter();r.encode(['benchmark warmup']);r.rerank_scores('benchmark',['benchmark evidence'])
        save(OUT/'method.json',M|{'warmup_ms':(time.perf_counter()-t)*1000,'embedding':r.EMBEDDING,'reranker':r.RERANKER,
             'rerank_max_tokens':r.RERANK_MAX_TOKENS,'retrieval':'30/30 -> RRF60 pool28 -> rerank12',
             'java_context_planner':'NOT_EXECUTED','source_labels':'existing source-aligned labels, not independent human gold',
             'tables':'separately reviewed from source page renders; atom recall is not table structural accuracy'})
        for i,d in enumerate(M['documents']):
            modes=['legacy_fast','legacy_docling','auto'];modes=modes[i%3:]+modes[:i%3]
            for mode in modes:
                rows.append(run(d,mode));save(OUT/'results.json',rows)
    finally:
        if r.milvus().has_collection(r.COLLECTION):r.milvus().drop_collection(r.COLLECTION)
        save(OUT/'cleanup.json',{'temporary_collection':r.COLLECTION,'exists':r.milvus().has_collection(r.COLLECTION)})
