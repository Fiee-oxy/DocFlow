"""Live, read-only comparison of original queries and frozen hypothetical docs."""
import hashlib,importlib.util,json,random,statistics,sys,time
from pathlib import Path
ROOT=Path('/work');OUT=ROOT/'evaluation/hyde40-20261112'
sys.path.insert(0,str(ROOT/'services/retrieval'))
import app as r
from query_expansion import QueryExpander
from retrieval_router import fuse
spec=importlib.util.spec_from_file_location('coverage',ROOT/'scripts/rag-coverage.py')
coverage=importlib.util.module_from_spec(spec);spec.loader.exec_module(coverage)

def save(name,data):(OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def merge(routes):
    values={};scores={}
    for route in routes:
        for rank,hit in enumerate(route,1):
            key=hit['chunk_id'];values.setdefault(key,hit);scores[key]=scores.get(key,0)+1/(60+rank)
    return [values[key] for key in sorted(values,key=lambda key:(-scores[key],key))[:28]]

def main():
    global OUT
    if '--nonthinking' in sys.argv:OUT=OUT/'nonthinking'
    cases=json.loads((ROOT/'evaluation/query-ab40-20261111/results-with-rewrite-only.json').read_text(encoding='utf-8'))['records']
    hypotheses=json.loads((OUT/'hypotheses.json').read_text(encoding='utf-8'));assert len(hypotheses)==40
    hyde={x['id']:x for x in hypotheses};assert set(hyde)=={x['case']['case_id'] for x in cases}
    r.query_expander=QueryExpander(str(ROOT/'services/retrieval/synonyms.json'),enabled=False)
    rng=random.Random(20261112);records=[];checked=set();stable=[]
    for row in cases:
        case=row['case'];scope=row['scope'];key=case['case_id'];hypo=hyde[key]
        chunks=json.loads((ROOT/'evaluation/rag-quality/outputs/recall-20261014/documents'/case['document_key']/'rag_chunks.json').read_text(encoding='utf-8'))
        lookup={c['chunk_id']:i for i,c in enumerate(chunks)}
        request=r.SearchRequest(question=case['question'],owner_id=scope['owner_id'],scopes=[dict(document_id=scope['doc_id'],index_version=scope['index_version'])],dense_top_k=30,sparse_top_k=30)
        scope_filter=r.scope_filter(request)
        if case['document_key'] not in checked:
            live=r.milvus().query(r.COLLECTION,filter=scope_filter,output_fields=['chunk_id'],limit=16384)
            assert {h['chunk_id'] for h in live}==set(lookup),'Corpus drift'
            checked.add(case['document_key'])
        def search(q):
            result=r.search(request.model_copy(update={'question':q}))
            return fuse(result['dense'],result['sparse'])
        def hyde_search():
            # Hypothetical DOCUMENT uses document embedding (no query prefix).
            # Invented answer words are not submitted to BM25 or inserted anywhere.
            began=time.perf_counter();vectors=r.encode([hypo['text']],query=False)
            embedding_ms=(time.perf_counter()-began)*1000;began=time.perf_counter()
            raw=r.milvus().search(r.COLLECTION,data=vectors,anns_field='dense',filter=scope_filter,limit=30,
                output_fields=['chunk_id','doc_id','index_version','section_id','content','search_text','sources'],
                search_params={'metric_type':'COSINE'},consistency_level='Strong')[0]
            hits=[]
            for hit in raw:
                entity=hit['entity'];entity['sources']=json.loads(entity['sources']);entity['score']=hit['distance'];hits.append(entity)
            return hits,dict(hydeEmbedding=embedding_ms,hydeDenseSearch=(time.perf_counter()-began)*1000)
        if not records:
            search(case['question'])
            if hypo['ok']:hyde_search()
        for repeat in range(3):
            modes=['ORIGINAL','ORIGINAL_HYDE','LLM_FROZEN','HYDE_ONLY'];rng.shuffle(modes)
            for mode in modes:
                began=time.perf_counter();timing={};valid=True
                if mode=='ORIGINAL':hits=search(case['question'])
                elif mode=='LLM_FROZEN':hits=merge([search(case['question'])]+[search(q) for q in row['rewrite'].get('queries',[])])
                elif mode=='HYDE_ONLY':
                    if hypo['ok']:hits,timing=hyde_search();hits=hits[:28]
                    else:hits=[];valid=False
                else:
                    original=search(case['question'])
                    if hypo['ok']:
                        extra,timing=hyde_search();hits=merge([original,extra[:28]])
                    else:hits=original
                elapsed=(time.perf_counter()-began)*1000
                assert all(h['doc_id']==scope['doc_id'] and h['index_version']==scope['index_version'] for h in hits)
                generation_ms=row['rewrite'].get('elapsedMs',0) if mode=='LLM_FROZEN' else hypo['generationMs'] if mode in ('ORIGINAL_HYDE','HYDE_ONLY') else 0
                records.append(dict(id=key,language=case['language'],size=case['group'],mode=mode,round=repeat,valid=valid,
                    retrievalMs=elapsed,generationMs=generation_ms,generationTiming='PRIOR_REWRITE_RUN' if mode=='LLM_FROZEN' else 'THIS_RUN_FROZEN_HYPOTHESIS',
                    timingMs=timing,top5Complete=coverage.coverage(hits[:5],case,lookup)['complete'],
                    poolComplete=coverage.coverage(hits,case,lookup)['complete'],hits=hits))
                if mode=='ORIGINAL':stable.append([h['chunk_id'] for h in hits[:5]]==[h['chunk_id'] for h in row['groups']['A']['hits'][:5]])
        save('results.json',dict(records=records,baselineStable=all(stable)))
        print(json.dumps(dict(done=len(records)//12,id=key)),flush=True)
    rows=[]
    for stratum in ('ALL','zh/small','zh/large','en/small','en/large'):
        for mode in modes:
            subset=[x for x in records if x['mode']==mode and (stratum=='ALL' or x['language']+'/'+x['size']==stratum)]
            per={x['id']:x for x in subset}
            assert all(len({x['top5Complete'] for x in subset if x['id']==key})==1 for key in per),'Unstable quality'
            rows.append(dict(stratum=stratum,mode=mode,questions=len(per),trials=len(subset),valid=sum(x['valid'] for x in per.values()),
                top5=sum(x['top5Complete'] for x in per.values()),pool28=sum(x['poolComplete'] for x in per.values()),
                retrievalP50=statistics.median(x['retrievalMs'] for x in subset),
                estimatedTotalP50=statistics.median(x['retrievalMs']+x['generationMs'] for x in subset)))
    paired={}
    for mode in ('ORIGINAL_HYDE','HYDE_ONLY','LLM_FROZEN'):
        gain=loss=0
        for row in cases:
            per={x['mode']:x for x in records if x['id']==row['case']['case_id'] and x['round']==0}
            gain+=not per['ORIGINAL']['top5Complete'] and per[mode]['top5Complete']
            loss+=per['ORIGINAL']['top5Complete'] and not per[mode]['top5Complete']
        paired[mode]=dict(gain=gain,loss=loss)
    save('summary.json',dict(groups=rows,paired=paired,trials=len(records),baselineStable=all(stable),
        generationSuccess=sum(x['ok'] for x in hypotheses),withAddedNumbers=sum(bool(x.get('addedNumberTokens')) for x in hypotheses),
        stoppedAtTokenLimit=sum(x.get('finishReason')=='length' for x in hypotheses),
        sourceHashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ('scripts/generate-hyde40.py','scripts/retrieve-hyde40.py','services/retrieval/app.py')}))

if __name__=='__main__':main()
