"""Complete per-rewrite Top-5 inspection, then summarize paired evidence results."""
import collections, importlib.util, json, statistics, sys, time
from pathlib import Path
ROOT=Path('/work');OUT=ROOT/'evaluation/query-ab40-20261111'
sys.path.insert(0,'/retrieval')
import app as r
from query_expansion import QueryExpander
from retrieval_router import fuse
spec=importlib.util.spec_from_file_location('quality',ROOT/'scripts/rag-coverage.py')
quality=importlib.util.module_from_spec(spec);spec.loader.exec_module(quality)

def save(name,data):
    (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    source='results-with-rewrite-only.json' if '--saved' in sys.argv else 'results.json'
    report=json.loads((OUT/source).read_text(encoding='utf-8'));records=report['records'];assert len(records)==40
    assert collections.Counter(x['case']['language']+'/'+x['case']['group'] for x in records)==dict.fromkeys(('zh/small','zh/large','en/small','en/large'),10)
    docs=collections.Counter(x['case']['document_key'] for x in records);assert len(docs)==20 and set(docs.values())=={2}
    for row in records:
        if '--saved' in sys.argv:
            assert 'rewriteOnly' in row
            continue
        case=row['case'];scope=row['scope'];raw=[]
        chunks=json.loads((ROOT/'evaluation/rag-quality/outputs/recall-20261014/documents'/case['document_key']/'rag_chunks.json').read_text(encoding='utf-8'))
        lookup={h['chunk_id']:i for i,h in enumerate(chunks)}
        original={h['chunk_id'] for h in row['groups']['A']['hits'][:5]}
        for query in row['rewrite'].get('queries',[]):
            r.query_expander=QueryExpander('/retrieval/synonyms.json',enabled=False)
            started=time.perf_counter()
            result=r.search(r.SearchRequest(question=query,owner_id=scope['owner_id'],scopes=[dict(document_id=scope['doc_id'],index_version=scope['index_version'])],dense_top_k=30,sparse_top_k=30))
            hits=fuse(result['dense'],result['sparse'])
            raw.append(dict(query=query,hits=hits,elapsedMs=(time.perf_counter()-started)*1000,
                overlapTop5=len(original & {h['chunk_id'] for h in hits[:5]})/5,
                top5Complete=quality.coverage(hits[:5],case,lookup),poolComplete=quality.coverage(hits,case,lookup)))
        row['rewriteOnly']=raw
        print('rewrite-only inspected '+case['case_id'],flush=True)
    save('results-with-rewrite-only.json',report)
    summary=[];review=[]
    for stratum in ('ALL','zh/small','zh/large','en/small','en/large'):
        rows=[x for x in records if stratum=='ALL' or x['case']['language']+'/'+x['case']['group']==stratum]
        for mode in ('A','B','C'):
            gains=sum(not x['groups']['A']['top5Complete']['complete'] and x['groups'][mode]['top5Complete']['complete'] for x in rows)
            losses=sum(x['groups']['A']['top5Complete']['complete'] and not x['groups'][mode]['top5Complete']['complete'] for x in rows)
            summary.append(dict(stratum=stratum,mode=mode,n=len(rows),top5Complete=sum(x['groups'][mode]['top5Complete']['complete'] for x in rows),
                pool28Complete=sum(x['groups'][mode]['poolComplete']['complete'] for x in rows),gain=gains,loss=losses,
                meanTop5Overlap=statistics.mean(x['groups'][mode]['top5OverlapWithA'] for x in rows),
                elapsedP50=statistics.median(x['groups'][mode]['elapsedMs'] for x in rows)))
    for row in records:
        a=row['groups']['A']
        reasons=[]
        for mode in ('B','C'):
            g=row['groups'][mode]
            if a['top5Complete']['complete']!=g['top5Complete']['complete']:reasons.append(mode+':TOP5_QUALITY_CHANGED')
            if a['poolComplete']['complete']!=g['poolComplete']['complete']:reasons.append(mode+':POOL_QUALITY_CHANGED')
        if any(x['overlapTop5']<=.4 for x in row['rewriteOnly']):reasons.append('REWRITE_ONLY_LOW_OVERLAP')
        if reasons:review.append(dict(case=row['case'],reasons=reasons,rewrite=row['rewrite'],
            groups={k:dict(top5=v['hits'][:5],top5Complete=v['top5Complete'],poolComplete=v['poolComplete']) for k,v in row['groups'].items()},rewriteOnly=row['rewriteOnly']))
    save('review-cases.json',review)
    rewrite_count=sum(len(x['rewriteOnly']) for x in records)
    result=dict(groups=summary,rewriteCalls=sum('elapsedMs' in x['rewrite'] for x in records),
        rewriteSuccess=sum(x['rewrite']['ok'] for x in records),acceptedRewriteQueries=rewrite_count,
        noAcceptedRewrite=sum(not x['rewrite'].get('queries') for x in records),
        aliasTriggered=sum(bool(x['groups']['B']['expansion']['terms']) for x in records),
        rewriteOnlyMeanOverlap=statistics.mean(y['overlapTop5'] for x in records for y in x['rewriteOnly']) if rewrite_count else None,
        reviewCases=len(review))
    save('summary.json',result)
    lines=['# 40 题查询扩展对照评测','',
        '## 实验边界','',
        '固定随机种子 20261111，20 份文档每份 2 道题。中文/英文 × 小/大文档四层各 10 题。大小沿用原题库标签，不重新定义；题型并不均衡（30 道 MULTI_FACT）。复用已有索引，只读，不重解析、不重建索引、不运行 Reranker，不生成答案。',
        'A：原始查询；B：原始查询 + 现有词典低权重 BM25 扩展；C：原始查询 + 最多两条模型改写，各自检索后 RRF 合并至 28 条。C 与 A/B 的融合方式不同，必须按整体策略解释，不能将差异全归因于改写措辞。原始查询始终参与；参与不等于其所有命中都能保留到 Top-5。',
        '模型仅接收问题，不接收原文、Gold 或答案。改写固定 temperature=0，过滤数字变化；实体和意图是否保持仍需人工核查。HyDE 未启用。',
        '','## 汇总','',
        '| 分层 | 组别 | 题数 | Top-5 完整 | 候选28完整 | Top-5改善/退化 | 与A重合度均值 | 耗时中位ms |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for x in summary:lines.append(f"|{x['stratum']}|{x['mode']}|{x['n']}|{x['top5Complete']}|{x['pool28Complete']}|{x['gain']}/{x['loss']}|{x['meanTop5Overlap']:.1%}|{x['elapsedP50']:.1f}|")
    allgroups={x['mode']:x for x in summary if x['stratum']=='ALL'}
    a,b,c=[allgroups[k] for k in ('A','B','C')]
    lines+=['','## 本轮结论','',
        f"原始查询 Top-5 完整 {a['top5Complete']}/40（{a['top5Complete']/40:.1%}），词典扩展 {b['top5Complete']}/40，模型多查询扩展 {c['top5Complete']}/40（{c['top5Complete']/40:.1%}）。模型扩展改善 {c['gain']} 题、退化 {c['loss']} 题，净增 3 题（7.5 个百分点），四道改善均在英文组。中文大文档组有一道退化。",
        '三组候选 Top-28 必要证据均完整 40/40，因此本轮收益主要体现为证据前移，不是找回候选池中原本缺失的证据。词典仅触发 9 题，未带来完整度改善，不能据此推断所有同义词扩展都无效。',
        '退化核查：华为盈利模式/发展边界问题的页 4 证据块，原始查询排名第 3，多查询融合后排名第 7，掉出 Top-5。扩展将问题分成两个子问题，其他相关块获得更多融合支持。原始查询保留一路仍不能保证其正确证据留在最终小窗口中。',
        f"单独改写查询与原始查询 Top-5 平均重合度为 {result['rewriteOnlyMeanOverlap']:.1%}；融合结果与原始查询为 {c['meanTop5Overlap']:.1%}。额外成本明显：A 中位 {a['elapsedP50']:.1f} ms，C 约 {c['elapsedP50']/1000:.2f} 秒。38 次改写成功、2 次 JSON 解析失败，失败时保留原始查询结果。",
        '建议保持默认关闭模型改写；本轮不证明为所有问题增加一次改写调用值得。可将英文多事实问题作为下一轮候选，但须先在独立文档验证，不能把本轮发现直接写成普遍收益。',
        '','## 如何解释','',
        '完整率使用冻结的页码和必要词项标注，尚非全部人工复核的答案正确率。Top-5 重合度为共同 Chunk ID 数/5，不表示相关性或正确性。单独改写查询的 Top-5 及其重合度保存在 results-with-rewrite-only.json；用于区分改写本身和融合效果。',
        '耗时为本轮单次顺序测量，不是稳定性能基准。C 包含一次远程模型改写和最多两次额外检索，模拟串行执行；原始检索耗时复用 A 的测量，不是完整 HTTP 请求。B 不调用 LLM。模型改写失败必须单独统计，不能当作有效扩展。',
        f"模型请求 {result['rewriteCalls']} 次，成功 {result['rewriteSuccess']} 次；接受改写 {rewrite_count} 条；{result['noAcceptedRewrite']} 题没有可用改写。词典扩展触发 {result['aliasTriggered']}/40 题。", 
        '评测不修改线上默认配置。查看 review-cases.json 核查退化、改善及低重合度案例；任何切换默认值的决定需独立留出文档验证。']
    (OUT/'analysis.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True),flush=True)

if __name__=='__main__':main()
