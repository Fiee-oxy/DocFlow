"""Offline report of HyDE, including generation failures and full provenance."""
import collections,hashlib,json,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'evaluation/hyde40-20261112';OUT=BASE/'nonthinking'
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def main():
    summary=read(OUT/'summary.json');records=read(OUT/'results.json')['records'];hypotheses=read(OUT/'hypotheses.json')
    old=read(ROOT/'evaluation/query-ab40-20261111/results-with-rewrite-only.json')['records']
    case_by_id={x['case']['case_id']:x for x in old};hypo_by_id={x['id']:x for x in hypotheses}
    assert len(records)==480 and len(hypotheses)==40
    assert collections.Counter(x['mode'] for x in records)==dict.fromkeys(('ORIGINAL','ORIGINAL_HYDE','HYDE_ONLY','LLM_FROZEN'),120)
    assert summary['baselineStable']
    review=[]
    for key,case in case_by_id.items():
        groups={x['mode']:x for x in records if x['id']==key and x['round']==0}
        if groups['ORIGINAL']['top5Complete']!=groups['ORIGINAL_HYDE']['top5Complete'] or not hypo_by_id[key]['ok']:
            review.append(dict(case=case['case'],hypothesis=hypo_by_id[key],
                groups={k:dict(top5Complete=v['top5Complete'],poolComplete=v['poolComplete'],top5=v['hits'][:5],
                    missingOriginalTop5=[dict(chunkId=h['chunk_id'],rank=i+1,pages=[s.get('pages') for s in h['sources']]) for i,h in enumerate(groups['ORIGINAL']['hits'][:5]) if h['chunk_id'] not in {z['chunk_id'] for z in v['hits'][:5]}]) for k,v in groups.items()}))
    (OUT/'review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf-8')
    allrows={x['mode']:x for x in summary['groups'] if x['stratum']=='ALL'}
    pilot=read(BASE/'hypotheses.json')
    lines=['# HyDE：40题同索引对照','',
        '## 实验设计','',
        '20份文档、每份2题；中文/英文×小/大文档四层各10题，沿用同一抽样和冻结Gold。三轮随机组别顺序，共480次检索。原始查询Top5与历史逐ID一致；先校验20份文档索引Chunk集合无漂移。',
        '本轮测试一种变体：每题一段假想回答，使用文档侧Embedding（query=False），只走Dense Top30；与原始Hybrid Top28进行等权query-level RRF(k=60)，固定输出28，再观察Top5。HyDE单路也裁至28用于诊断。不是多假想文档向量平均等所有HyDE变体的评测。',
        '保留原问题一路；假想文本不做BM25、不写数据库、不作为证据、不生成最终回答，不运行Reranker或父块扩展。LLM改写对照复用上次保存的文本，重新执行检索；两次历史改写失败按原始查询回退。',
        '模型只接收问题，不接收原文、引用、Gold。这里故意允许假想值用于检索，因此新增数字不等于已核实事实。所有证据仍来自真实文档块。文档是公开资料，不能排除模型预训练见过资料，不能据此评价未知私有文档的回答能力。',
        '','## 生成有效性','',
        f"初轮使用API默认思考模式：40次请求中仅{sum(x['ok'] for x in pilot)}条有可用正文，14条为空，10条可用正文触及输出上限。初轮保留作配置失败记录，不作为主要效果结论。随后对全部40题统一设置thinking.type=disabled，同提示词、同384 Token预算重跑，没有按检索效果选择性重试。两轮共80次生成请求。",
        f"正式非思考组：{summary['generationSuccess']}/40生成成功，{summary['stoppedAtTokenLimit']}条触及输出上限；{summary['withAddedNumbers']}条含问题没有的数字字符串。新增数字仅作风险诊断，未逐项确认真伪。失败时原始+HyDE组退回原始检索，HyDE单路计为无结果。",
        '参数参考：[DeepSeek思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。初轮空正文只记录生成校验失败，未保存完整服务返回，不能逐条追溯其内部原因。',
        '','## 主要结果','',
        '| 方案 | Top5完整 | 候选28完整 | 检索P50 ms | 生成+检索估计P50 ms |',
        '|---|---:|---:|---:|---:|']
    for mode in ('ORIGINAL','HYDE_ONLY','ORIGINAL_HYDE','LLM_FROZEN'):
        x=allrows[mode];lines.append(f"|{mode}|{x['top5']}/40|{x['pool28']}/40|{x['retrievalP50']:.1f}|{x['estimatedTotalP50']:.1f}|")
    lines+=['','## 分层结果','', '| 分层 | 原始 | 原始+HyDE | LLM改写 |','|---|---:|---:|---:|']
    for stratum in ('zh/small','zh/large','en/small','en/large'):
        groups={x['mode']:x for x in summary['groups'] if x['stratum']==stratum}
        lines.append('|'+stratum+'|'+ '|'.join(f"{groups[k]['top5']}/10" for k in ('ORIGINAL','ORIGINAL_HYDE','LLM_FROZEN'))+'|')
    pair=summary['paired']['ORIGINAL_HYDE'];a=allrows['ORIGINAL'];h=allrows['ORIGINAL_HYDE'];llm=allrows['LLM_FROZEN']
    lines+=['','## 解读','',
        f"原始+HyDE相对原始查询改善{pair['gain']}题、退化{pair['loss']}题，净变化{h['top5']-a['top5']}题（{(h['top5']-a['top5'])/40*100:+.1f}个百分点）。与LLM改写的{llm['top5']}/40相比，当前HyDE组合为{h['top5']}/40。质量按40个独立问题计数，不把3轮重复当120题。",
        '本轮建议：不默认开启HyDE，也不以HyDE单路替代原始检索。中文小文档从9/10降为7/10，英文两组各从6/10升为8/10；收益存在分层差异。原始+HyDE的Top28仍40/40完整，主要表现为排序变化。茅台假想数字错误与Top5退化同时发生，但没有做替换数值消融，不能断言退化完全由错误数字导致。',
        'Top5重排收益和候选池召回必须分开看。只要正确证据本来就在Top28，就不能将其进入Top5说成找回完全缺失证据。均为原文词项与物理页Gold完整率，不是回答正确率；Gold尚需独立人工复核。',
        '假想文档可能不保持原始实体/年份，也可能产生错误数值；本轮不会把生成文本正确与否用作过滤条件，否则会借用Gold挑选假设。实际改善、退化题及前后原文和页码保存在review.json供核查。',
        '具体核查：贵州茅台研发人数问题，假想文本写1,096人、2.28%，但物理页11真实块407980ea71559e8f63a386c3b8a373ca18cba96924c76a0a0f58f9d3b223bc94写801人、2.31%。这是合成文本不能作为答案证据的直接例子。相反，3M假想文本写出1,577，与标注吻合；公开资料可能被模型记住，不能因此推断未知私有文档也能如此。',
        '时间口径：每题假想文档只生成一次，检索重复三轮。生成+检索列为逐题实测生成时间与当前检索时间相加的估计，不是完整HTTP/首Token耗时；LLM改写生成时间来自历史运行。模型/日期/思考模式不同会影响比较，不能宣传严格同环境的生成速度对比。',
        '小样本、单次生成，只用于这版方案筛选，不证明生产稳定P95或统计显著性。HyDE和模型改写都没有接入生产默认链路。']
    (OUT/'analysis.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    validation=dict(status='PASS',questions=40,trials=480,corpusWrites=0,productionChanged=False,baselineStable=True,
        sourceHashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ('scripts/generate-hyde40.py','scripts/retrieve-hyde40.py','scripts/report-hyde40.py')},
        hypothesesSha256=hashlib.sha256((OUT/'hypotheses.json').read_bytes()).hexdigest(),totalGenerationRequests=80)
    (OUT/'validation.json').write_text(json.dumps(validation,indent=2),encoding='utf-8')
    print(json.dumps(dict(overall=list(allrows.values()),paired=summary['paired'],generationSuccess=summary['generationSuccess']),ensure_ascii=True))
if __name__=='__main__':main()
