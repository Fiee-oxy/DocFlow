"""Generate query-only hypothetical passages for offline retrieval, never evidence."""
import hashlib,json,re,sys,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evaluation/hyde40-20261112'
PROMPT=('Write one short hypothetical document passage that would answer the search question. '
        'Use the SAME language and preserve the entity, period, units and all requested aspects. '
        'Write answer-like prose as if it appeared in a document, not alternative search queries. '
        'This is a synthetic retrieval aid, not a verified answer: details or values may be hypothetical. '
        'Do not claim to have read the actual document, do not cite sources and do not add a preamble. '
        'Keep it within 80-140 English words or 120-220 Chinese characters. Return plain text only.')
def main():
    global OUT
    nonthinking='--nonthinking' in sys.argv
    if nonthinking:OUT=OUT/'nonthinking'
    OUT.mkdir(parents=True,exist_ok=True)
    src=ROOT/'evaluation/query-ab40-20261111/results-with-rewrite-only.json'
    cases=json.loads(src.read_text(encoding='utf-8'))['records'];assert len(cases)==40
    cfg={}
    for line in (ROOT/'.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1);cfg[k.strip()]=v.strip().strip('\"').strip("'")
    path=OUT/'hypotheses.json';saved=json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    known={x['id'] for x in saved};blocked=False
    protocol=dict(questions=40,sampleSourceSha256=hashlib.sha256(src.read_bytes()).hexdigest(),
        model=cfg.get('LLM_MODEL'),temperature=0,maxTokens=384,prompt=PROMPT,thinking='disabled' if nonthinking else 'API_DEFAULT',
        input='QUESTION_ONLY_NO_DOCUMENT_NO_GOLD',hypothesesPerQuestion=1,
        originalQueryRetained=True,documentEmbedding=True,hydeBranch='DENSE_ONLY_TOP30',
        finalCandidateBudget=28,reranker=False,productionEnabled=False)
    (OUT/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in cases:
        key=row['case']['case_id'];question=row['case']['question']
        if key in known:continue
        item=dict(id=key,question=question,synthetic=True,isEvidence=False)
        start=time.perf_counter()
        if blocked:item.update(ok=False,failure='CIRCUIT_OPEN')
        else:
            try:
                payload=dict(model=cfg['LLM_MODEL'],temperature=0,max_tokens=384,messages=[dict(role='system',content=PROMPT),dict(role='user',content=question)])
                if nonthinking:payload['thinking']={'type':'disabled'}
                request=urllib.request.Request(cfg['SILICONFLOW_BASE_URL'].rstrip('/')+'/chat/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+cfg['SILICONFLOW_API_KEY']})
                with urllib.request.urlopen(request,timeout=45) as response:data=json.load(response)
                text=data['choices'][0]['message']['content'].strip()
                item.update(usage=data.get('usage'),finishReason=data['choices'][0].get('finish_reason'),contentLength=len(text))
                if not text or len(text)>6000:raise ValueError('INVALID_PASSAGE_SIZE')
                original_numbers=set(re.findall(r'\d+(?:[.,]\d+)*',question))
                item.update(ok=True,text=text,usage=data.get('usage'),finishReason=data['choices'][0].get('finish_reason'),
                    addedNumberTokens=sorted(set(re.findall(r'\d+(?:[.,]\d+)*',text))-original_numbers))
            except urllib.error.HTTPError as e:
                item.update(ok=False,status=e.code)
                if e.code in (401,402,403):blocked=True
            except Exception as e:item.update(ok=False,failure=type(e).__name__)
        item['generationMs']=(time.perf_counter()-start)*1000;saved.append(item)
        path.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(done=len(saved),id=key,ok=item['ok'],failure=item.get('failure'),status=item.get('status'))),flush=True)
if __name__=='__main__':main()
