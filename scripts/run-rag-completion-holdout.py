"""Whole-document holdout acceptance; never calls a chat LLM. Do not tune on these results."""
import argparse,hashlib,importlib.util,json,time,statistics
from pathlib import Path
def module(name,filename):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(filename));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
p=module('pilot','run-rag-quality-pilot.py');evaluation=module('natural','run-rag-natural-evaluation.py')
RUN=p.BASE/'outputs/completion-holdout-20261014'
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--wait-for');args=parser.parse_args()
    if args.wait_for:
        print(json.dumps({'event':'waiting_for_regression_before_holdout_ingestion'}),flush=True)
        deadline=time.monotonic()+7200
        while not Path(args.wait_for).exists():
            if time.monotonic()>deadline:raise TimeoutError('Regression did not finish')
            time.sleep(5)
    cases=json.loads((RUN/'cases.json').read_text(encoding='utf-8'));freeze=json.loads((RUN/'freeze.json').read_text(encoding='utf-8'))
    assert hashlib.sha256((RUN/'cases.json').read_bytes()).hexdigest()==freeze['sha256']
    items=json.loads((RUN/'corpus.json').read_text(encoding='utf-8'))
    for item in items:
        assert hashlib.sha256((p.BASE/item['path']).read_bytes()).hexdigest()==item['sha256']
        assert all(c['source_sha256']==item['sha256'] for c in cases if c['document_key']==item['id'])
    session=p.load_session(RUN);p.ingest(RUN,session,items)
    cache={item['id']:{name:json.loads((RUN/'documents'/item['id']/name).read_text(encoding='utf-8')) for name in ['parsed.json','rag_chunks.json']} for item in items}
    p.save(RUN/'runtime.json',p.api('/documents/query/diagnostics',token=session['token']))
    results=[]
    for case in cases:
        target=RUN/'previews'/(case['case_id']+'.json')
        if target.exists():raw=json.loads(target.read_text(encoding='utf-8'))
        else:
            raw=p.api('/knowledge/search',{'documentIds':[session['documents'][case['document_key']]['document_id']],'question':case['question'],'limit':200},session['token'])
            assert raw['llmCalls']==0;p.save(target,raw)
        result=evaluation.assess(case,raw,cache);results.append(result)
        p.save(RUN/'stage-results.json',results)
        print(json.dumps({'event':'holdout_case_finished','case':case['case_id'],'first_incomplete':result['first_incomplete_stage']}),flush=True)
    summary={'tested':len(results),'groups':{},'llmCalls':0}
    for lang in ['zh','en']:
        rows=[r for r in results if r['language']==lang]
        summary['groups'][lang]={'tested':len(rows),**{stage:sum(r['stages'][stage]['complete'] for r in rows) for stage in ['parsed','chunked','candidate_union','rrf','topK','context']}}
    summary['median_seconds']=statistics.median(r['latencyMs']/1000 for r in results)
    p.save(RUN/'summary.json',summary);audit=p.audit(session);assert audit['llmCalls']==0 and audit['queryTasks']==0;p.save(RUN/'no-llm-audit.json',audit)
    lines=['# 新整份文档留出测试','',f"完成 {len(results)} 题，中英文各6题；常规入库与评测没有调用聊天 LLM。",'','文档：央行2024年第四季度货币政策报告（59页，1.27MB）；RFC9110（194页，2.86MB）。整份文档未参与前面的调参；问题与必要原文已在首次检索前冻结。','',
           '这是小规模泛化检查，仍由助手标注，未经过独立人工复核。词面证据齐全不等于最终回答正确，表格需核对行列关系。','']
    for lang,g in summary['groups'].items():lines.append(f"- {lang}：候选完整 {g['candidate_union']}/{g['tested']}；精排后 {g['topK']}/{g['tested']}；上下文完整 {g['context']}/{g['tested']}。")
    lines+=['','## 逐题结果','']
    for case,result in zip(cases,results):
        lines +=[f"- {case['question']}：{result['first_incomplete_stage']}；必要原文物理页 {case['units'][0]['pages']}。"]
    (RUN/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'event':'holdout_finished','summary':summary}),flush=True)
if __name__=='__main__':main()
