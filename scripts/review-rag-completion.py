"""Summarize frozen A/B runs; apply only previously source-verified unit alternatives."""
import json,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'evaluation/rag-quality/outputs'
def main():
    spec=importlib.util.spec_from_file_location('natural',ROOT/'scripts/run-rag-natural-evaluation.py');n=importlib.util.module_from_spec(spec);spec.loader.exec_module(n)
    for directory in ['completion-ab-20261014','completion-context-replay-20261014']:
        run=BASE/directory;path=run/'review-overrides.json'
        review=json.loads(path.read_text(encoding='utf-8'))
        for row in review['reviews']:
            if row['case_id']=='perf-ebe504c582757dd5-q1':row['required_atoms']=['2023',"annual revenue",'CNY704,174 million']
            if row['case_id']=='perf-eac932022de3335e-q1':row['required_atoms']=['2025年','销售收入','880,941','百万元']
        path.write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf-8')
        n.RUN=run;cases=json.loads((run/'cases.json').read_text(encoding='utf-8'))
        cache={key:{f:json.loads((n.EXPORTS/key/f).read_text(encoding='utf-8')) for f in ['parsed.json','rag_chunks.json']} for key in {c['document_key'] for c in cases}}
        results={}
        for case in cases:
            preview=run/'previews'/(case['case_id']+'.json')
            if preview.exists():results[case['case_id']]=n.assess(case,json.loads(preview.read_text(encoding='utf-8')),cache)
        n.report(cases,results)
        if 'context-replay' in directory:
            report=run/'report.md'
            report.write_text('> **上下文独立回放**：固定原来的召回、融合和精排，只执行新的 Java 上下文选择代码；没有重新检索或调用模型。下文耗时为原历史请求耗时，不代表本次执行速度。\n\n'+report.read_text(encoding='utf-8'),encoding='utf-8')
        summary=json.loads((run/'summary.json').read_text(encoding='utf-8'))
        print(json.dumps({'run':directory,'tested':summary['tested'],'raw_complete':sum(r['stages']['context']['complete'] for r in results.values()),'reviewed_complete':summary['reviewed_context_complete']}))
if __name__=='__main__':main()
