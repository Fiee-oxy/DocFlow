"""Paired quality diagnostics; no inference and no post-result parameter tuning."""
import json,re,statistics,unicodedata
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'evaluation/chunk-expanded-20261019'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
from evidence_metrics import coverage,source_order
rows=read(ROOT/'results.json');manifest=read(ROOT/'manifest.json')
cases={c['case_id']:c for d in manifest['documents'] for c in d['cases']}
queries=[]
for row in rows:
    order=source_order(read(ROOT/row['document']/row['strategy']/'chunks.json'))
    for q in row['queries']:
        q=dict(q,strategy=row['strategy'],language=row['language'],document=row['document'])
        case=cases[q['case_id']];folder=ROOT/row['document']/row['strategy']
        context=read(folder/(q['case_id']+'-context.json'))
        q['context']=coverage(context['evidence'],case,order)
        q['budgets']={str(b['availableBudget']):coverage(b['evidence'],case,order) for b in read(folder/(q['case_id']+'-budget-sweep.json'))}
        queries.append(q)
summary={};paired=[]
for strategy in ['legacy','boundary']:
    qs=[q for q in queries if q['strategy']==strategy]
    original=[q for q in qs if q['variant']=='ORIGINAL'];paraphrase=[q for q in qs if q['variant']=='PARAPHRASE']
    def aggregate(xs):
        return {'n':len(xs),**{k:sum(q[k]['complete'] for q in xs) for k in ['parse','chunk','candidate','rrf','rerank_top12','context']},
            'mean_atom_coverage_top12':statistics.mean(q['rerank_top12']['atoms_found']/q['rerank_top12']['atoms_total'] for q in xs) if xs else None,
            'coverage_at_k':{str(k):sum(q['coverage_at_k'][str(k)]['complete'] for q in xs) for k in [1,3,5,8,12,28]},
            'context_budget':{str(k):sum(q['budgets'][str(k)]['complete'] for q in xs) for k in [3000,8000,26761]}}
    groups={group:aggregate([q for q in original if q['group']==group]) for group in ['ADDITIONAL_FINANCIAL','DOCUMENT_HOLDOUT','DOCLING_INPUT']}
    changes=[]
    for q in paraphrase:
        base=next(x for x in original if x['case_id']==q['base_case_id'])
        if base['context']['complete']!=q['context']['complete']:
            changes.append({'base_case_id':q['base_case_id'],'before':base['context']['complete'],'after':q['context']['complete'],'original':base['question'],'paraphrase':q['question']})
    per_document={doc:aggregate([q for q in original if q['document']==doc]) for doc in sorted({q['document'] for q in original})}
    summary[strategy]={'per_document':per_document,'macro_document_complete_rate':statistics.mean(v['context']/v['n'] for v in per_document.values()),'original':aggregate(original),'paraphrase':aggregate(paraphrase),'groups':groups,
        'languages':{lang:aggregate([q for q in original if q['language']==lang]) for lang in ['zh','en']},
        'paraphrase_flips':changes,'chunks':sum(r['chunks'] for r in rows if r['strategy']==strategy),
        'mean_search_ms':statistics.mean(q['search_ms'] for q in qs),'mean_rerank_ms':statistics.mean(q['rerank_ms'] for q in qs),
        'mean_total_ms':statistics.mean(q['total_ms'] for q in qs),
        'budget_sweep':{str(target):{'complete':sum(s['complete'] for r in rows if r['strategy']==strategy for s in r.get('budget_sweep',[]) if s['target_tokens']==target),
            'questions':sum(s['questions'] for r in rows if r['strategy']==strategy for s in r.get('budget_sweep',[]) if s['target_tokens']==target),
            'chunks':sum(s['chunks'] for r in rows if r['strategy']==strategy for s in r.get('budget_sweep',[]) if s['target_tokens']==target)} for target in [256,350,450]}}
for a in [q for q in queries if q['strategy']=='legacy']:
    b=next(q for q in queries if q['strategy']=='boundary' and q['case_id']==a['case_id'])
    if a['context']['complete']!=b['context']['complete']:
        paired.append({'question':a['question'],'case_id':a['case_id'],'legacy':a['context']['complete'],'boundary':b['context']['complete']})
summary['paired_changes']=paired
summary['all_runs_complete']=len(rows)==12 and all(r['status']=='COMPLETE' for r in rows)
summary['all_collections_removed']=all(r['collection_removed'] for r in rows)
(ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
(ROOT/'evaluated-queries.json').write_text(json.dumps(queries,ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# 扩展分块评测','',
    '## 结论与解释','',
    '继续保留原策略。原始题最终证据完整率为21/24，新策略20/24；改写题为9/12和10/12。两组合并恰好都是30/36，但这种合并会掩盖“丢一道原题、补一道改写题”的交换，不能据此说两种方法完全等价，也不能说新策略更好。',
    '',
    '新策略让3M资本支出原题漏在候选召回，改善了HTTP Retry-After改写题。财报组退步，留出文档和Docling组没有总体提升。原策略24道原题的候选都齐全，2道在RRF丢失、1道在精排Top12丢失，当前优先问题仍是融合和排序，而不是解析丢字。',
    '',
    '新策略改写翻转从5对减少至3对，不能直接解释为稳定性提高：其中3M原先“原题成功、改写失败”变为“两者都失败”，同样减少了翻转。要同时看每对问题是否都成功。',
    '',
    '3000预算下新策略20/24、原策略19/24，说明更细的边界在紧预算下有局部收益；8000及较大预算下恢复为原策略21/24、新策略20/24。这不足以证明应该全局切换。',
    '',
    '精排Top3到Top12在本组原题没有增加完整命中，Top28只多救回1题；并非简单增大Top-K就能解决所有失败。256/350/450三个分块目标均保留24/24原题的全部原文，但仅350做了检索比较，不能把450块数更少直接当作检索收益。',
    '',
    '同时修复了评测器按检索排名拼接跨块短语的缺陷，改为按原文顺序且只拼接连续块。修正了货币政策报告1题在候选/RRF两个阶段的误判，不改变其最终命中；上一轮18题经同规则复核仍为两组17/18。修正前数据保留在带rank-order-matcher-discarded后缀的文件中。',
    '', '6份文档，24道原始题＋12道等义改写，两个策略共72次检索，未调用聊天LLM。改写题不是12个独立知识点。',
    '', '固定相同保存的解析输入，Dense30/BM2530、RRF28、Reranker256Token/Top12。每文档/策略使用独立新collection。',
    '', '## 原始题的阶段结果','','|阶段|原策略|句段边界策略|','|---|---:|---:|']
for key,label in [('parse','解析'),('chunk','分块'),('candidate','候选'),('rrf','融合'),('rerank_top12','精排Top12'),('context','最终上下文')]:
    lines.append(f'|{label}|{summary["legacy"]["original"][key]}/24|{summary["boundary"]["original"][key]}/24|')
lines+=['','## 语言与部分证据覆盖','','|指标|原策略|新策略|','|---|---:|---:|']
for lang in ['zh','en']:
    a=summary['legacy']['languages'][lang];b=summary['boundary']['languages'][lang]
    lines.append(f'|{lang}最终上下文完整|{a["context"]}/{a["n"]}|{b["context"]}/{b["n"]}|')
lines.append(f'|Top12必要文本原子平均覆盖率|{summary["legacy"]["original"]["mean_atom_coverage_top12"]:.1%}|{summary["boundary"]["original"]["mean_atom_coverage_top12"]:.1%}|')
lines.append(f'|每份文档等权完整率|{summary["legacy"]["macro_document_complete_rate"]:.1%}|{summary["boundary"]["macro_document_complete_rate"]:.1%}|')
lines+=['','## 分组：最终上下文证据完整','','|测试组|原策略|句段边界策略|','|---|---:|---:|']
for group,label in [('ADDITIONAL_FINANCIAL','新增财报'),('DOCUMENT_HOLDOUT','整份文档留出'),('DOCLING_INPUT','Docling结构输入')]:
    a=summary['legacy']['groups'][group];b=summary['boundary']['groups'][group]
    lines.append(f'|{label}|{a["context"]}/{a["n"]}|{b["context"]}/{b["n"]}|')
a=summary['legacy']['paraphrase'];b=summary['boundary']['paraphrase'];lines.append(f'|等义改写|{a["context"]}/{a["n"]}|{b["context"]}/{b["n"]}|')
lines+=['','## 证据排名曲线（24道原始题）','','|精排保留数量|原策略完整题数|新策略完整题数|','|---|---:|---:|']
for k in ['1','3','5','8','12','28']:
    lines.append(f'|Top-{k}|{summary["legacy"]["original"]["coverage_at_k"][k]}|{summary["boundary"]["original"]["coverage_at_k"][k]}|')
lines+=['','Top28仅用于诊断排序损失；没有据此调大生产Top-K。','', '## 改写稳定性','']
for strategy in ['legacy','boundary']:
    changes=summary[strategy]['paraphrase_flips'];lines.append(f'- {strategy}：12对等义问题中，{len(changes)}对发生完整/不完整翻转。')
    for c in changes:lines.append(f'  - {c["original"]} → {c["paraphrase"]}：{c["before"]} → {c["after"]}')
lines+=['','## 两种策略的差异题','']
for c in paired:lines.append(f'- {c["question"]}：原策略完整={c["legacy"]}，新策略完整={c["boundary"]}。')
if not paired:lines.append('没有出现完整证据命中变化。')
lines+=['','## 分块目标参数扫描（不重新检索）','','|目标Token|原策略证据完整/块数|新策略证据完整/块数|','|---|---:|---:|']
for k in ['256','350','450']:
    a=summary['legacy']['budget_sweep'][k];b=summary['boundary']['budget_sweep'][k]
    lines.append(f'|{k}|{a["complete"]}/{a["questions"]}，{a["chunks"]}块|{b["complete"]}/{b["questions"]}，{b["chunks"]}块|')
lines+=['','这里只测试保留与块数，不能推断256或450的检索准确率。','', '## 实际Java上下文预算回放','','|可用预算（字节上界计数）|原策略完整/24|新策略完整/24|','|---|---:|---:|']
for k in ['3000','8000','26761']:
    lines.append(f'|{k}|{summary["legacy"]["original"]["context_budget"][k]}|{summary["boundary"]["original"]["context_budget"][k]}|')
lines+=['','## 耗时','']
for strategy in ['legacy','boundary']:
    s=summary[strategy];lines.append(f'- {strategy}：检索均值{s["mean_search_ms"]:.1f}ms，精排{s["mean_rerank_ms"]/1000:.2f}s，检索+融合+精排{s["mean_total_ms"]/1000:.2f}s。')
lines+=['','## 范围与局限','','本轮未修改生产参数或策略实现。留出文档是未参与本轮分块策略调试的整份文件，但在更早的项目评测中用过，不是全项目从未见过的数据。Docling组使用原始结构化事件，另4份使用保存的原文单位重建批次，均未重新解析。','','匹配依据是来源页码与必要文本原子，不是生成答案评分或表格行列结构评分；原子匹配仍可能把同页不相干的数字算作命中，需要逐题人工审核。排名曲线统计累计证据完整，不冒充单一相关段落的MRR。没有把改写题当作独立样本计算显著性。','','上下文回放使用实际Java代码、固定预算，不含真实聊天历史、章节扩展或LLM生成。耗时单轮、串行，含负载波动，不是并发P95。']
(ROOT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k in ['all_runs_complete','all_collections_removed','paired_changes']},ensure_ascii=False))
for strategy in ['legacy','boundary']:print(strategy,json.dumps(summary[strategy]['original']))
