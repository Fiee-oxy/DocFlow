"""Read-only, stratified query expansion experiment. Run inside retrieval image."""
import collections, hashlib, importlib.util, json, os, random, re, sys, time, urllib.request, urllib.error
from pathlib import Path

ROOT=Path('/work');OUT=ROOT/'evaluation/query-ab40-20261111'
sys.path.insert(0,'/retrieval')
import app as r
from query_expansion import QueryExpander
from retrieval_router import fuse
spec=importlib.util.spec_from_file_location('quality',ROOT/'scripts/rag-coverage.py')
quality=importlib.util.module_from_spec(spec);spec.loader.exec_module(quality)

def save(name,data):
    (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def sample():
    cases=json.loads((ROOT/'evaluation/rag-quality/outputs/natural-90-20261014/cases.json').read_text(encoding='utf-8'))
    rng=random.Random(20261111);result=[]
    for lang in ('zh','en'):
        for size in ('small','large'):
            docs=collections.defaultdict(list)
            for c in cases:
                if c['language']==lang and c['group']==size:docs[c['document_key']].append(c)
            keys=sorted(docs);rng.shuffle(keys)
            for key in keys[:5]:
                pool=sorted(docs[key],key=lambda c:c['case_id']);rng.shuffle(pool);result+=pool[:2]
    assert len(result)==40
    save('sample.json',result)
    save('protocol.json',dict(seed=20261111,strata=dict(collections.Counter(c['language']+'/'+c['group'] for c in result)),
        documents=len({c['document_key'] for c in result}),maxQuestionsPerDocument=2,denseTopK=30,bm25TopK=30,fusionTopK=28,
        comparisonTopK=5,reranker=False,hyde=False,indexWrites=False,
        source='natural-90-20261014',gold='SOURCE_ALIGNED_PENDING_HUMAN_REVIEW',
        definition='top5 overlap = shared chunk IDs / 5; incomplete lists reported separately'))
    return result

def cfg():
    result={}
    for line in (ROOT/'.env').read_text(encoding='utf-8-sig').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            k,v=line.split('=',1);result[k.strip()]=v.strip().strip('\"').strip("'")
    return result

def rewrite(question,config):
    # Send only the public benchmark question, never reference text, gold or documents.
    payload=dict(model=config.get('LLM_MODEL'),temperature=0,max_tokens=320,messages=[
        dict(role='system',content='Rewrite this document search question into at most two alternative search queries in the SAME language. Preserve all entities, dates, numbers, units, scope and requested facts. Do not answer, infer facts, invent context or generate a hypothetical document. Return only JSON: {"queries":["..."]}.'),
        dict(role='user',content=question)])
    req=urllib.request.Request(config['SILICONFLOW_BASE_URL'].rstrip('/')+'/chat/completions',
        data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+config['SILICONFLOW_API_KEY'],'Content-Type':'application/json'})
    began=time.perf_counter()
    try:
        with urllib.request.urlopen(req,timeout=45) as response:data=json.load(response)
        raw=data['choices'][0]['message']['content'].strip()
        raw=re.sub(r'^```(?:json)?\s*|\s*```$','',raw)
        values=json.loads(raw)['queries'];queries=[]
        numbers=set(re.findall(r'\d+(?:\.\d+)?',question))
        for q in values[:2]:
            if not isinstance(q,str) or not q.strip() or len(q)>1500:continue
            if set(re.findall(r'\d+(?:\.\d+)?',q))!=numbers:continue
            if q!=question and q not in queries:queries.append(q.strip())
        return dict(ok=True,queries=queries,elapsedMs=(time.perf_counter()-began)*1000,usage=data.get('usage'))
    except urllib.error.HTTPError as e:
        return dict(ok=False,status=e.code,elapsedMs=(time.perf_counter()-began)*1000)
    except Exception as e:return dict(ok=False,failure=type(e).__name__,elapsedMs=(time.perf_counter()-began)*1000)

def main():
    OUT.mkdir(parents=True,exist_ok=True);cases=sample();config=cfg();records=[];documents={};breaker=False
    for case in cases:
        key=case['document_key']
        if key not in documents:
            chunks=json.loads((ROOT/'evaluation/rag-quality/outputs/recall-20261014/documents'/key/'rag_chunks.json').read_text(encoding='utf-8'))
            ids=[c['chunk_id'] for c in chunks]
            matched=r.milvus().get(r.COLLECTION,ids=ids[:8],output_fields=['doc_id','owner_id','index_version'])
            if not matched:raise RuntimeError('Saved document missing from index: '+key)
            scope={k:matched[0][k] for k in ('doc_id','owner_id','index_version')}
            live=r.milvus().query(r.COLLECTION,filter=f'doc_id in {json.dumps([scope["doc_id"]])} and index_version in {json.dumps([scope["index_version"]])}',output_fields=['chunk_id'],limit=16384)
            if {x['chunk_id'] for x in live}!=set(ids):raise RuntimeError('Index/corpus mismatch: '+key)
            documents[key]=(scope,{c['chunk_id']:i for i,c in enumerate(chunks)})
        scope,order=documents[key]
        def search(q,enabled):
            r.query_expander=QueryExpander('/retrieval/synonyms.json',enabled=enabled)
            req=r.SearchRequest(question=q,owner_id=scope['owner_id'],scopes=[dict(document_id=scope['doc_id'],index_version=scope['index_version'])],dense_top_k=30,sparse_top_k=30)
            start=time.perf_counter();response=r.search(req)
            return dict(elapsedMs=(time.perf_counter()-start)*1000,hits=fuse(response['dense'],response['sparse']),expansion=response['query_expansion'],timingMs=response['timingMs'])
        # Warm model/index outside timing; no reranker initialization.
        if not records:search(case['question'],False)
        base=search(case['question'],False);alias=search(case['question'],True)
        rewritten=dict(ok=False,skipped='CONFIG_UNAVAILABLE_OR_CIRCUIT_OPEN') if breaker or not config.get('SILICONFLOW_API_KEY') else rewrite(case['question'],config)
        if rewritten.get('status') in (401,402,403):breaker=True
        alternatives=[search(q,False) for q in rewritten.get('queries',[])]
        # Equal fixed output budget; original route always participates in RRF.
        routes=[base]+alternatives;values={};scores={}
        for route in routes:
            for rank,h in enumerate(route['hits'],1):
                k=h['chunk_id'];values.setdefault(k,h);scores[k]=scores.get(k,0)+1/(60+rank)
        merged=[values[k] for k in sorted(values,key=lambda k:(-scores[k],k))[:28]]
        multi=dict(hits=merged,elapsedMs=base['elapsedMs']+sum(x['elapsedMs'] for x in alternatives)+rewritten.get('elapsedMs',0))
        groups=dict(A=base,B=alias,C=multi)
        for label,value in groups.items():
            value['top5Complete']=quality.coverage(value['hits'][:5],case,order)
            value['poolComplete']=quality.coverage(value['hits'],case,order)
            value['top5OverlapWithA']=len({h['chunk_id'] for h in base['hits'][:5]} & {h['chunk_id'] for h in value['hits'][:5]})/5
            value['top5Count']=len(value['hits'][:5])
        records.append(dict(case=case,scope=scope,rewrite=rewritten,groups=groups))
        save('results.json',dict(scope='LIVE_READ_ONLY_MILVUS_NO_RERANK_NO_ANSWER_LLM',records=records))
        print(json.dumps(dict(done=len(records),id=case['case_id'],rewriteOk=rewritten['ok'],rewriteStatus=rewritten.get('status')),ensure_ascii=True),flush=True)
    save('source-hashes.json',{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ('scripts/query-expansion-ab40.py','services/retrieval/app.py','services/retrieval/query_expansion.py','services/retrieval/synonyms.json')})

if __name__=='__main__':main()
