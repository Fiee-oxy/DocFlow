"""Live single-channel confidence fallback checks. No chat/model task creation."""
import importlib.util,json,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evaluation/low-confidence-fallback-20261112'
spec=importlib.util.spec_from_file_location('e',Path(__file__).with_name('final-e2e.py'));e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)

def internal(body):
    request=urllib.request.Request('http://127.0.0.1:5002/retrieve-routed',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

def main():
    original=e.OUT;actors=json.loads((original/'accepted-run/session.private.json').read_text(encoding='utf-8'))['actors']
    case=next(c for c in json.loads((original/'accepted-run/ingestion.json').read_text(encoding='utf-8'))['prepared'] if c['kind']=='pdf')
    actor=actors[case['actor']];token=actor['token'];checks=[]
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        try:urllib.request.urlopen(e.BASE+'/health',timeout=2).close();break
        except Exception:time.sleep(1)
    else:raise RuntimeError('Backend not ready')
    def preview(question):
        request=urllib.request.Request(e.BASE+'/documents/query/preview',data=json.dumps(dict(documentIds=[case['doc']],question=question,limit=50)).encode(),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
        with urllib.request.urlopen(request,timeout=210) as response:result=json.load(response)
        assert result['code']==0,result
        return result['data']
    def record(name,result,is_preview=True):
        trace=result['retrieval'][0] if is_preview else result;routing=trace['routing']
        for hit in trace.get('topK',trace.get('selected',[])):
            assert hit['doc_id']==case['doc'] and hit['index_version']==case['state']['version']
        if is_preview:assert result['llmCalls']==0
        checks.append(dict(name=name,routing=routing,sourceCount=result.get('selectedSourceCount'),llmCalls=0))
        (OUT/'live-results.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8');print(name+' observed',flush=True)
        return routing
    high='Find "'+case['code']+'"'
    result=preview(high);route=record('BM25_HIGH_CONFIDENCE',result)
    assert route['initialRoute']=='BM25' and route['quality']['accepted'] and not route['fallback']
    weak='Find "'+case['code']+'" "UNSEEN-ZZ991122"'
    result=preview(weak);route=record('BM25_NONEMPTY_LOW_CONFIDENCE',result)
    assert result['retrieval'][0]['bm25'] and route['initialRoute']=='BM25' and route['fallback']
    assert route['fallbackReason']=='LOW_CONFIDENCE' and route['fallbackChannel']=='DENSE'
    assert route['fallbackAttempts']==1 and route['finalRoute']=='HYBRID'
    assert result['retrieval'][0]['bm25'][0]['chunk_id'] in {h['chunk_id'] for h in result['retrieval'][0]['rrf']}
    result=preview('What are the recovery code and rate limiting algorithm?');route=record('HYBRID_DEFAULT_UNCHANGED',result)
    assert route['initialRoute']=='HYBRID' and not route['fallback']
    # Actual model scores with a deliberately strict boundary threshold. This is
    # a control-flow test, not evidence that the default .45 is calibrated.
    body=dict(owner_id=actor['ownerId'],scopes=[dict(document_id=case['doc'],index_version=case['state']['version'])],
        question='Find semantically similar text about saving committed work.',dense_min_score=1.0)
    result=internal(body);route=record('DENSE_THRESHOLD_BOUNDARY_1_0',result,False)
    assert result['dense'] and route['initialRoute']=='DENSE' and route['fallbackChannel']=='BM25'
    assert 'DENSE_SIMILARITY_LOW' in route['quality']['reasons'] and route['fallbackAttempts']==1
    body.update(question=weak,fallback_on_low_confidence=False,dense_min_score=.45)
    result=internal(body);route=record('LOW_CONFIDENCE_SWITCH_DISABLED',result,False)
    assert route['quality']['reason']=='LOW_CONFIDENCE' and not route['fallback'] and not result['dense']
    summary=dict(passed=True,liveChecks=len(checks),chatLlmCalls=0,defaultBm25MinCoverage=.55,defaultDenseMinScore=.45,
        denseBoundaryTestThreshold=1.0,note='Rule confidence; no calibrated accuracy or latency improvement claimed')
    (OUT/'live-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
