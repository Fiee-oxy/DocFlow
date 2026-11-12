"""Re-score frozen responses only; never queries a model, index, or database."""
import importlib.util,json,collections
from pathlib import Path
spec=importlib.util.spec_from_file_location('natural',Path(__file__).with_name('run-rag-natural-evaluation.py'));e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
BASE=e.BASE/'outputs';report={}
for name in ['natural-90-20261014','completion-ab-20261014','completion-context-replay-20261014','completion-holdout-20261014']:
    run=BASE/name;cases=json.loads((run/'cases.json').read_text(encoding='utf-8'))
    export=run/'documents' if name=='completion-holdout-20261014' else e.EXPORTS
    cache={key:{file:json.loads((export/key/file).read_text(encoding='utf-8')) for file in ['parsed.json','rag_chunks.json']} for key in {c['document_key'] for c in cases}}
    rows=[e.assess(case,json.loads((run/'previews'/(case['case_id']+'.json')).read_text(encoding='utf-8')),cache) for case in cases]
    violations=[r['case_id'] for r in rows if r['stages']['topK']['complete'] and not r['stages']['rrf']['complete']]
    report[name]={'tested':len(rows),'raw_context_complete':sum(r['stages']['context']['complete'] for r in rows),'subset_monotonicity_violations':violations,
                  'first_incomplete_stages':dict(collections.Counter(r['first_incomplete_stage'] for r in rows))}
    if name=='natural-90-20261014':
        target=BASE/'completion-release-20261014';target.mkdir(exist_ok=True)
        (target/'baseline-rescored-stage-results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    assert not violations,(name,violations)
target=BASE/'completion-release-20261014'
(target/'metric-audit.json').write_text(json.dumps({'method':'Original labels and responses unchanged; reconstruct atoms only across real adjacent source blocks, independent of relevance order. Original baseline files retained.','runs':report},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report))
