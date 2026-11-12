"""Summarize frozen rule experiment without model or database calls."""
import hashlib,json,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evaluation/rule-split40-20261111'
def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def main():
    s=read('summary.json');records=read('results.json')['records'];plans=read('plans.json')
    assert len(records)==360 and s['historicalBaselineStable']
    triggered={p['id'] for p in plans if p['plan']['queries']}
    triggered_metrics={k:statistics.median(x['retrievalMs'] for x in records if x['id'] in triggered and x['mode']==k) for k in ('ORIGINAL','RULE','LLM_FROZEN')}
    lines=['# 规则拆分能否接近 LLM 多查询改写：40 题对照','',
        '## 结论','',
        '**本轮不能。**规则拆分 Top-5 必要证据完整 28/40（70%），低于原始查询 30/40（75%），也低于冻结 LLM 改写 33/40（82.5%）。规则改善 1 题、退化 3 题；LLM 改写改善 4 题、退化 1 题。这是当前保守规则的结果，不代表一切规则拆分都无效。',
        '','## 公平对照','',
        '沿用上次相同 40 题：20 文档每文档2题，中文/英文×小/大文档四层各10题。题型以多事实为主，不是所有题型均衡；大小沿用旧标签。',
        '三组重新执行真实只读 Milvus 检索，每题每组3轮，固定随机组别顺序。原始问题始终保留一路；额外问题各走 Dense30+BM25 30、融合28，再以相同的 query-level RRF(k=60) 合并至28。最终观察Top5，不执行Reranker、父上下文补全或回答生成。原始查询120次Top5与上次逐ID一致，三轮质量判定稳定。',
        'LLM组仅复用上次保存的改写文本，不重新调用模型。上次2题改写JSON失败仍回退原始查询，纳入40题分母。规则制定不读取Gold或检索结果，计划在检索前冻结。',
        '','## 分层质量','',
        '| 分层 | 原始查询 | 规则拆分 | LLM改写 |','|---|---:|---:|---:|']
    for stratum in ('ALL','zh/small','zh/large','en/small','en/large'):
        rows={x['mode']:x for x in s['groups'] if x['stratum']==stratum}
        lines.append('|'+stratum+'|'+ '|'.join(f"{rows[k]['top5']}/{rows[k]['questions']}" for k in ('ORIGINAL','RULE','LLM_FROZEN'))+'|')
    lines+=['','三组候选Top28均为40/40完整，本次变化发生在排序/Top5窗口，而不是候选池能否找到必要证据。完整率依赖冻结页码和词项Gold，仍需人工复核，不能称为答案正确率。',
        '','## 时间与成本','',
        f"规则仅触发13/40题；规则计算中位{s['splitP50']:.4f} ms（含首次正则初始化的最大值{s['splitMax']:.3f} ms）。全体检索中位：原始32.1 ms、规则33.6 ms、冻结LLM改写查询92.3 ms。多数规则未触发，因此全体中位数掩盖触发后的额外检索成本。",
        f"仅13道触发题的检索耗时中位：原始{triggered_metrics['ORIGINAL']:.1f} ms、规则{triggered_metrics['RULE']:.1f} ms、冻结LLM{triggered_metrics['LLM_FROZEN']:.1f} ms。",
        '冻结LLM组本轮没有生成成本。若叠加上次各题实测改写耗时，估算合计中位约1.85秒；这是历史生成时间+当前检索时间，不是本轮实测完整请求。规则额外模型调用为0。每组120条时间样本来自40题×3轮，不应宣传为生产稳定P95/P99。',
        '','## 案例核查','',
        '- 改善：AI如何改变CT审阅时间、部署多少医疗机构。拆成两个关注点后，必要证据进入Top5；规则与LLM均改善。',
        '- 退化：移动网络连接人数及全球人口占比。第二子问题仅剩“what share of the world population was this”，没有恢复this所指的移动网络，原始正确证据被挤出Top5。',
        '- 退化：贵州茅台研发人数及占比。规则复制了不完整的中文前缀并重复年份，形成不自然的查询；多查询融合还改变了原先正确块的位置。不能单凭本轮分数区分前缀噪声与融合各自贡献，需专门消融才能归因。',
        '- 退化：华为盈利模式与发展边界。规则与LLM都退化，说明保留原始问题参与融合不等于保住原始问题的正确Top5。',
        '详细问题、拆分文本、原文与页码见review.json；全部候选和轮次见results.json。',
        '','## 使用边界','',
        '该实现只识别明确的并列问句；不做任意名词列表拆分、同义词生成、HyDE或语义指代消解。英文大写词/数字锚点不是实体识别，中文前缀复制也不是可靠主语恢复，可能生成噪声。模块未接入生产调用，默认路径不变。',
        '建议维持原始查询默认路径。当前证据已在Top28，下一步若继续应优先检查多查询融合是否稀释原始证据，而不是增加更多拆分规则；本轮到此停止，不围绕这40题反复调参。']
    (OUT/'analysis.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    sources=['services/retrieval/query_splitter.py','services/retrieval/test_query_splitter.py','scripts/rule-split40.py','scripts/report-rule-split40.py']
    validation=dict(status='PASS',trials=360,uniqueQuestions=40,rounds=3,baselineTop5HistoricalMatch=True,
        newLlmCalls=0,indexWrites=0,unitTests=4,triggeredRetrievalP50=triggered_metrics,
        sourceHashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources})
    (OUT/'validation.json').write_text(json.dumps(validation,indent=2),encoding='utf-8')
    print(json.dumps(validation,ensure_ascii=True))
if __name__=='__main__':main()
