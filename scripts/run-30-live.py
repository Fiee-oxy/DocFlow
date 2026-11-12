import json,time,urllib.request
from pathlib import Path
src=Path('evaluation/rag-quality/outputs/performance-20261013'); out=Path('evaluation/rag-quality/outputs/live-30-'+time.strftime('%Y%m%d-%H%M%S')); out.mkdir(parents=True); (out/'previews').mkdir()
s=json.loads((src/'.session.json').read_text(encoding='utf-8'))
login=urllib.request.Request('http://127.0.0.1:9090/user/login',data=json.dumps({'username':s['username'],'password':s['password']}).encode(),headers={'Content-Type':'application/json'})
s['token']=json.load(urllib.request.urlopen(login))['data']['token']
cases=json.loads(Path('evaluation/rag-quality/outputs/fixed-top30-rerank256-20261016/cases.json').read_text(encoding='utf-8'))[:30]; rows=[]
for c in cases:
 doc=s['documents'][c['document_key']]['document_id']; t=time.perf_counter(); req=urllib.request.Request('http://127.0.0.1:9090/documents/query/preview',data=json.dumps({'documentIds':[doc],'question':c['question'],'limit':200},ensure_ascii=False).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+s['token']}); d=json.load(urllib.request.urlopen(req,timeout=300))['data']; ms=round((time.perf_counter()-t)*1000,1); d['measured_total_ms']=ms; (out/'previews'/(c['case_id']+'.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); tr=(d.get('retrieval') or [{}])[0]; rows.append({'case_id':c['case_id'],'total_ms':ms,'server_latency_ms':d.get('latencyMs'),'llmCalls':d.get('llmCalls',0),'rrf':len(tr.get('rrf',[])),'reranked':len(tr.get('reranked',[]))}); print(json.dumps(rows[-1],ensure_ascii=False),flush=True)
(out/'timings.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8'); print('OUT',out)
