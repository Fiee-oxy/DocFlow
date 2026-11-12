"""Compare saved previews with the same cases and strict source scoring; no API calls."""
import json, importlib.util, math, statistics
from pathlib import Path
base=Path('evaluation/rag-quality/outputs/fixed-top30-rerank256-20261016')
run=Path('evaluation/rag-quality/outputs/live-30-20261016-161543')
spec=importlib.util.spec_from_file_location('coverage',Path('scripts/rag-coverage.py'))
cov=importlib.util.module_from_spec(spec); spec.loader.exec_module(cov)
cases=json.loads((base/'cases.json').read_text(encoding='utf-8'))[:30]
def score(rows,case):
    for u in case['units']:
        eligible=[r for r in rows if not u['pages'] or set(u['pages']) & set(r.get('pages',[]) or [p for s in r.get('sources',[]) for p in s.get('pages',[])])]
        text=cov.sc.norm('\n'.join(r.get('content',r.get('text','')) for r in eligible))
        if not all(cov.sc.norm(a) in text for a in u['atoms']): return False
    return True
rows=[]
for c in cases:
    item={'case_id':c['case_id'],'question':c['question']}
    for label,folder in [('baseline',base),('current',run)]:
        path=folder/'previews'/(c['case_id']+'.json')
        if not path.exists(): continue
        p=json.loads(path.read_text(encoding='utf-8')); t=p['retrieval'][0]
        item[label]={'latencyMs':p['latencyMs'],'llmCalls':p['llmCalls'],**{s:score(t.get(s,[]),c) for s in ['rrf','reranked','topK']},'candidate':score(t.get('dense',[])+t.get('bm25',[]),c),'context':score(p.get('evidence',[]),c)}
    rows.append(item)
summary={}
for label in ['baseline','current']:
    values=[r[label] for r in rows if label in r]; ts=sorted(v['latencyMs'] for v in values)
    summary[label]={'completed':len(values),'medianMs':statistics.median(ts),'p95Ms':ts[math.ceil(.95*len(ts))-1],'minMs':min(ts),'maxMs':max(ts),'llmCalls':sum(v['llmCalls'] for v in values),**{s:sum(v[s] for v in values) for s in ['candidate','rrf','reranked','topK','context']}}
result={'scoring':'strict lexical atoms restricted to annotated physical pages, unchanged for both runs','summary':summary,'cases':rows}
(run/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
