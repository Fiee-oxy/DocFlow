"""Re-score saved inference traces with original-order contiguous evidence matching."""
import json,sys
from pathlib import Path
from evidence_metrics import coverage,source_order
root=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[1]/'evaluation/chunk-expanded-20261019'
rows=json.loads((root/'results.json').read_text(encoding='utf-8'))
manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
cases={c['case_id']:c for d in manifest['documents'] for c in d['cases']}
archive=root/'results-rank-order-matcher-discarded.json'
if not archive.exists():archive.write_bytes((root/'results.json').read_bytes())
changes=[]
def sources(chunks):return [s for c in chunks for s in c.get('sources',[])]
for row in rows:
    folder=root/row['document']/row['strategy'];chunks=json.loads((folder/'chunks.json').read_text(encoding='utf-8'));order=source_order(chunks)
    for q in row['queries']:
        path=folder/(q['case_id']+'.json');data=json.loads(path.read_text(encoding='utf-8'));case=cases[q['case_id']]
        old=path.with_name(path.stem+'-rank-order-matcher-discarded.json')
        if not old.exists():old.write_bytes(path.read_bytes())
        union={c['chunk_id']:c for c in data['search']['dense']+data['search']['sparse']}
        pools={'chunk':chunks,'candidate':list(union.values()),'rrf':data['rrf'],'rerank_top12':data['ranked'][:12]}
        for stage,pool in pools.items():
            value=coverage(sources(pool),case,order)
            if value['complete']!=q[stage]['complete']:changes.append({'case_id':q['case_id'],'strategy':row['strategy'],'stage':stage,'before':q[stage]['complete'],'after':value['complete']})
            q[stage]=value
        q['coverage_at_k']={str(k):coverage(sources(data['ranked'][:k]),case,order) for k in [1,3,5,8,12,28]}
        q['first_complete_rank']=next((k for k in range(1,len(data['ranked'])+1) if coverage(sources(data['ranked'][:k]),case,order)['complete']),None)
        q['matcher']='SOURCE_ORDER_CONTIGUOUS_V2'
        assert not q['rerank_top12']['complete'] or q['rrf']['complete']
        assert not q['rrf']['complete'] or q['candidate']['complete']
        data['result']=q;path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
(root/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
(root/'matcher-corrections.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'corrected_stage_verdicts':len(changes),'changes':changes}))
