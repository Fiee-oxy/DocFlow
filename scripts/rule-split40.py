"""Paired read-only retrieval: original, frozen LLM queries, deterministic clauses."""
import collections, hashlib, importlib.util, json, random, statistics, sys, time
from pathlib import Path
ROOT=Path('/work');OUT=ROOT/'evaluation/rule-split40-20261111'
sys.path.insert(0,str(ROOT/'services/retrieval'))
import app as r
from query_splitter import split_query, VERSION
from query_expansion import QueryExpander
from retrieval_router import fuse
spec=importlib.util.spec_from_file_location('quality',ROOT/'scripts/rag-coverage.py')
quality=importlib.util.module_from_spec(spec);spec.loader.exec_module(quality)

def save(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def merge(routes):
    scores={};values={}
    for hits in routes:
        for rank,h in enumerate(hits,1):
            key=h['chunk_id'];values.setdefault(key,h);scores[key]=scores.get(key,0)+1/(60+rank)
    return [values[k] for k in sorted(values,key=lambda k:(-scores[k],k))[:28]]

def main():
    frozen=ROOT/'evaluation/query-ab40-20261111/results-with-rewrite-only.json'
    old=json.loads(frozen.read_text(encoding='utf-8'))['records'];assert len(old)==40
    plans=[]
    for row in old:
        start=time.perf_counter();plan=split_query(row['case']['question']);elapsed=(time.perf_counter()-start)*1000
        plans.append(dict(id=row['case']['case_id'],question=row['case']['question'],plan=plan,splitMs=elapsed))
    # Freeze all plans before retrieval: rule design never consults gold or hits.
    save('plans.json',plans)
    save('protocol.json',dict(ruleVersion=VERSION,questions=40,languages={'zh':20,'en':20},strata='10 per language/size',
        rounds=3,seed=20261111,topDense=30,topBm25=30,fusion=28,final=5,reranker=False,newLlmCalls=0,
        originalAlwaysParticipates=True,sharedMerge='RRF k=60, unweighted query routes',
        frozenSourceSha256=hashlib.sha256(frozen.read_bytes()).hexdigest(),
        ruleSha256=hashlib.sha256((ROOT/'services/retrieval/query_splitter.py').read_bytes()).hexdigest()))
    r.query_expander=QueryExpander(str(ROOT/'services/retrieval/synonyms.json'),enabled=False)
    rng=random.Random(20261111);records=[];checked=set();consistency=[]
    for row,planned in zip(old,plans):
        case=row['case'];scope=row['scope'];doc=case['document_key']
        chunks=json.loads((ROOT/'evaluation/rag-quality/outputs/recall-20261014/documents'/doc/'rag_chunks.json').read_text(encoding='utf-8'))
        lookup={c['chunk_id']:i for i,c in enumerate(chunks)}
        if doc not in checked:
            live=r.milvus().query(r.COLLECTION,filter=f'owner_id in [{int(scope["owner_id"])}] and doc_id in {json.dumps([scope["doc_id"]])} and index_version in {json.dumps([scope["index_version"]])}',output_fields=['chunk_id'],limit=16384)
            assert {c['chunk_id'] for c in live}==set(lookup), 'Corpus drift: '+doc
            checked.add(doc)
        def search(q):
            result=r.search(r.SearchRequest(question=q,owner_id=scope['owner_id'],scopes=[dict(document_id=scope['doc_id'],index_version=scope['index_version'])],dense_top_k=30,sparse_top_k=30))
            return fuse(result['dense'],result['sparse'])
        if not records:search(case['question'])
        variants=dict(ORIGINAL=[],RULE=planned['plan']['queries'],LLM_FROZEN=row['rewrite'].get('queries',[]))
        for repeat in range(3):
            modes=list(variants);rng.shuffle(modes)
            for mode in modes:
                began=time.perf_counter();routes=[search(case['question'])]+[search(q) for q in variants[mode]]
                hits=merge(routes);retrieval_ms=(time.perf_counter()-began)*1000
                for h in hits:
                    assert h['doc_id']==scope['doc_id'] and h['index_version']==scope['index_version']
                records.append(dict(id=case['case_id'],language=case['language'],size=case['group'],mode=mode,round=repeat,
                    retrievalMs=retrieval_ms,splitMs=planned['splitMs'] if mode=='RULE' else 0,
                    historicalRewriteMs=row['rewrite'].get('elapsedMs',0) if mode=='LLM_FROZEN' else 0,
                    queryCount=len(routes),queries=[case['question']]+variants[mode],hits=hits,
                    top5Complete=quality.coverage(hits[:5],case,lookup)['complete'],
                    poolComplete=quality.coverage(hits,case,lookup)['complete']))
                if mode=='ORIGINAL':
                    consistency.append(dict(id=case['case_id'],round=repeat,
                        sameHistoricalTop5=[h['chunk_id'] for h in hits[:5]]==[h['chunk_id'] for h in row['groups']['A']['hits'][:5]]))
        save('results.json',dict(records=records,consistency=consistency))
        print(json.dumps(dict(done=len(checked),caseId=case['case_id'],records=len(records),split=bool(variants['RULE'])),ensure_ascii=True),flush=True)
    rows=[];review=[]
    for stratum in ('ALL','zh/small','zh/large','en/small','en/large'):
        for mode in variants:
            subset=[x for x in records if x['mode']==mode and (stratum=='ALL' or x['language']+'/'+x['size']==stratum)]
            unique={x['id']:x for x in subset}
            assert all(len({r['top5Complete'] for r in subset if r['id']==k})==1 for k in unique), 'Unstable quality'
            rows.append(dict(stratum=stratum,mode=mode,questions=len(unique),trials=len(subset),top5=sum(x['top5Complete'] for x in unique.values()),
                pool28=sum(x['poolComplete'] for x in unique.values()),retrievalP50=statistics.median(x['retrievalMs'] for x in subset),
                queryCountMean=statistics.mean(x['queryCount'] for x in subset),
                estimatedTotalP50=statistics.median(x['retrievalMs']+x['splitMs']+x['historicalRewriteMs'] for x in subset)))
    for row,planned in zip(old,plans):
        candidates={x['mode']:x for x in records if x['id']==row['case']['case_id'] and x['round']==0}
        if len({x['top5Complete'] for x in candidates.values()})>1 or planned['plan']['queries']:
            review.append(dict(case=row['case'],plan=planned['plan'],llmQueries=row['rewrite'].get('queries',[]),
                groups={k:dict(complete=v['top5Complete'],top5=v['hits'][:5],poolComplete=v['poolComplete']) for k,v in candidates.items()}))
    comparisons={}
    for mode in ('RULE','LLM_FROZEN'):
        gain=loss=0
        for case in old:
            pair={x['mode']:x for x in records if x['id']==case['case']['case_id'] and x['round']==0}
            gain+=not pair['ORIGINAL']['top5Complete'] and pair[mode]['top5Complete']
            loss+=pair['ORIGINAL']['top5Complete'] and not pair[mode]['top5Complete']
        comparisons[mode]=dict(gain=gain,loss=loss,net=gain-loss)
    summary=dict(groups=rows,paired=comparisons,splitTriggered=sum(bool(x['plan']['queries']) for x in plans),
        splitP50=statistics.median(x['splitMs'] for x in plans),splitMax=max(x['splitMs'] for x in plans),
        historicalBaselineStable=all(x['sameHistoricalTop5'] for x in consistency),trials=len(records),newLlmCalls=0)
    save('summary.json',summary);save('review.json',review)
    print(json.dumps(summary,ensure_ascii=True),flush=True)

if __name__=='__main__':main()
