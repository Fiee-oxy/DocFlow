"""Analyzed release report, keeping raw scores and source-verified unit alternatives separate."""
import collections,hashlib,json,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'evaluation/rag-quality/outputs'
def read(run,name):return json.loads((BASE/run/name).read_text(encoding='utf-8'))
def valid_reviews(run):
    import importlib.util
    spec=importlib.util.spec_from_file_location('score',ROOT/'scripts/score-rag-quality-pilot.py');sc=importlib.util.module_from_spec(spec);spec.loader.exec_module(sc)
    valid=set()
    for review in read(run,'review-overrides.json')['reviews']:
        raw=read(run,'previews/'+review['case_id']+'.json')
        atoms=review.get('required_atoms',[review['retrieved']])
        if any(review['retrieved_page'] in s.get('pages',[]) and all(sc.norm(a) in sc.norm(s.get('text','')) for a in atoms) for s in raw.get('evidence',[])):valid.add(review['case_id'])
    return valid
def main():
    old='natural-90-20261014';new='completion-ab-20261014';hold='completion-holdout-20261014'
    assert (BASE/old/'cases.json').read_bytes()==(BASE/new/'cases.json').read_bytes()
    before={r['case_id']:r for r in read(old,'stage-results.json')};after={r['case_id']:r for r in read(new,'stage-results.json')}
    assert len(after)==90
    old_valid={r['case_id'] for r in read(old,'review-overrides.json')['reviews'] if r['decision']=='VALID_ALTERNATIVE_CONTEXT'}
    new_valid=valid_reviews(new)
    def complete(row,valid):return row['stages']['context']['complete'] or row['case_id'] in valid
    gains=[key for key in after if not complete(before[key],old_valid) and complete(after[key],new_valid)]
    regressions=[key for key in after if complete(before[key],old_valid) and not complete(after[key],new_valid)]
    incomplete=[r for r in after.values() if not complete(r,new_valid)]
    failures=dict(collections.Counter(r['first_incomplete_stage'] for r in incomplete))
    old_summary=read(old,'summary.json');summary=read(new,'summary.json');held=read(hold,'summary.json')
    report={'baseline_complete':sum(complete(r,old_valid) for r in before.values()),'final_complete':sum(complete(r,new_valid) for r in after.values()),'gained_cases':gains,'regressed_cases':regressions,'remaining_first_incomplete_stages':failures,'holdout':held,'no_chat_llm':all(read(run,'no-llm-audit.json')['llmCalls']==0 for run in [old,new,hold]),'labels':'Lexical evidence completeness plus individually checked unit alternatives, not answer accuracy; independent human adjudication pending.'}
    smoke_lines=(ROOT/'server/target/completion-live-retrieval-smoke.log').read_text(encoding='utf-8',errors='replace').splitlines()
    smoke=next(json.loads(line) for line in reversed(smoke_lines) if line.startswith('{') and '"passed"' in line)
    report['live_retrieval_contract_checks']=smoke
    directory=BASE/'completion-release-20261014';directory.mkdir(exist_ok=True)
    (directory/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    labels={'candidate_union':'候选召回','rrf':'融合池','topK':'最终Top-K','context':'上下文','service':'服务状态','parsed':'解析','chunked':'分块'}
    lines=['# 本轮补齐结果与分析','',f"同一冻结90题：必要证据完整数由 **{report['baseline_complete']}/90** 变为 **{report['final_complete']}/90**。新增完整 {len(gains)} 题，退化 {len(regressions)} 题。","这是证据是否齐全的检查，不是聊天回答准确率；全程未调用聊天 LLM。",'',
      '## 已修复什么','',
      '- 上下文独立回放固定原召回与精排，只执行新 Java 预算代码，63/90 → 82/90，原先19道上下文丢证据均恢复。模型 Evidence 仅包含 ID 与正文，必要章节与文件版本提示单独传入，复杂来源元数据保留在服务端。',
      '- 预算由固定事实上限改为按实际召回正文动态扩展，默认最高20,000字节且受模型总窗口约束。仍是保守字节上界，不是精确 Tokenizer；证据不足或超预算仍会显式报告。',
      '- 双路各召回40条，融合池40条，最终Top-12；增加通用关键词视图、单路高位候选保护和显式RRF分数。没有针对某份文档写答案或业务别名。',
      '- 当前Milvus实例上，小米手册45块存在多字段等值组合过滤少6块的异常；单值IN逻辑等价过滤恢复45块。30份文档审计中，IN返回ID均与数据库一致，范围均合法。不能据此推断所有Milvus版本都有此缺陷。',
      '', '## 仍未解决的质量损失','']
    for stage,count in failures.items():lines.append(f'- 首次不完整在{labels.get(stage,stage)}：{count}题。')
    for row in incomplete:
        missing=[a for u in row['stages']['context']['units'] for a in u['missing']]
        lines.append(f"- {row['question']}：{labels.get(row['first_incomplete_stage'],row['first_incomplete_stage'])}；词面缺失：{'；'.join(missing)}。")
    lines +=['',f"另外，真实本地检索的 {smoke['count']} 项链路契约检查通过，覆盖索引幂等、BM25、章节/账号/版本隔离及真实HybridChunker。但独立简短语义题“如何防止瞬间大量访问压垮接口”的Top-1选中了异步处理，而非预期的令牌桶限流。不能据此宣称精排模型已全面验收；应纳入下一轮模型对比集。"]
    lines +=['','## 整份文档留出检查','', '新文档：央行2024年第四季度报告（59页，1.27MB）；RFC9110（194页，2.86MB）。中英文各6题，首次检索前固定标注，没有根据其结果调参。']
    for lang,group in held['groups'].items():lines.append(f"- {lang}：候选完整 {group['candidate_union']}/{group['tested']}，最终上下文完整 {group['context']}/{group['tested']}。")
    lines +=['','样本仅两份且仍由助手标注，不能据此声称任意文件精准。表格行列、规范性词语位置及合法替代答案仍需要人工复核。','',
      '评分复核：原评分器按相关性返回顺序拼接片段，曾把中文成交额题误记为融合缺失。现在只按真实来源块顺序连接相邻片段，禁止跨无关块拼接；原文、问题、标注与响应均未修改。复核后该题实际成功；原90题最终完整数保持不变。详见metric-audit.json。','',
      '## 耗时与成本','',f"冻结回归请求耗时中位数：原 {old_summary['median_seconds']} 秒，本轮 {summary['median_seconds']} 秒；本轮P95 {summary['p95_seconds']} 秒。这是本机端到端检索耗时，包含模型推理，首个请求含模型加载；不是远程8核服务器的性能承诺。", 
      '扩大候选池与证据预算有成本：精排更慢，实际模型原文输入可能比原固定小预算增加。新链路限制相关证据规模，避免大文档全文发送，但没有宣称同时降低所有问题的Token与延迟。','',
      '## 知识库与企业边界','',
      '- 已迁移个人默认知识库，支持新建库、库内文档、独立版本上传、历史版本和选库/选文档提问。新版本所需索引未就绪时保留旧可用版本。',
      '- 统一检索入口与权限/身份适配接口已接入；现有登录身份为唯一默认身份来源。真实HTTP检查覆盖越权、范围交集和身份伪造拒绝。',
      '- 知识库聊天复用SSE，事件游标重连复用同一任务；多文档任务逐份报告进度，事件流批量校验权限。',
      '- 当前只实现个人所有权，尚未开放共享ACL、Dify/IM适配或多企业租户隔离。真实聊天模型验收仍待可用模型余额。','',
      '详细数据保留在原始预览、stage-results.json、scope-filter-audit.json、独立上下文回放与留出测试目录中。']
    (directory/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['baseline_complete','final_complete','remaining_first_incomplete_stages','no_chat_llm']}))
if __name__=='__main__':main()
