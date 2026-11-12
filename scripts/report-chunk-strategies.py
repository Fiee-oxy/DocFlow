"""Summarize frozen chunk A/B runs and actual Java context replay."""
import json,statistics,re,unicodedata
from pathlib import Path
root=Path(__file__).resolve().parents[1]
out=root/'evaluation/chunk-strategies-20261019'
rows=json.loads((out/'results.json').read_text(encoding='utf-8'))
manifest=json.loads((root/'evaluation/parser-ab-20261018/manifest.json').read_text(encoding='utf-8'))
cases={c['case_id']:c for d in manifest['documents'] for c in d['cases']}
def norm(s):return re.sub(r'[^\w\u3400-\u9fff]','',unicodedata.normalize('NFKC',s).casefold())
def complete(evidence,case):
 return all(norm(atom) in norm('\n'.join(e.get('text','') for e in evidence if set(e.get('pages',[]))&set(unit['pages']))) for unit in case['units'] for atom in unit['atoms'])
summary={}
for strategy in ['legacy','boundary']:
 runs=[r for r in rows if r['strategy']==strategy];qs=[q for r in runs for q in r['queries']]
 final=0
 for r in runs:
  for q in r['queries']:
   data=json.loads((out/r['document']/strategy/(q['case_id']+'-context.json')).read_text(encoding='utf-8'))
   q['context_complete']=complete(data['evidence'],cases[q['case_id']]);final+=q['context_complete']
 summary[strategy]={'questions':len(qs),'documents':len(runs),'chunks':sum(r['chunks'] for r in runs),**{stage:sum(q[stage]['complete'] for q in qs) for stage in ['parse','chunk','candidate','rrf','rerank_top12']},'context':final,'chunk_ms':sum(r['chunk_ms'] for r in runs),'index_ms':sum(r['index_ms'] for r in runs),'search_mean_ms':statistics.mean(q['search_ms'] for q in qs),'rrf_mean_ms':statistics.mean(q['rrf_ms'] for q in qs),'rerank_mean_ms':statistics.mean(q['rerank_ms'] for q in qs),'query_mean_ms':statistics.mean(q['total_ms'] for q in qs)}
(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# 分块策略对照结果','', '固定6份PDF、18道已有证据题，中文/英文各9题。相同FAST解析结果，350/480Token，Dense30 + BM2530 → RRF28 → Reranker256/Top12。不调用聊天LLM。','', '| 指标 | 原策略 | 句段边界策略 |','|---|---:|---:|']
for key,label in [('parse','解析证据完整'),('chunk','分块证据完整'),('candidate','候选证据完整'),('rrf','RRF证据完整'),('rerank_top12','精排Top12证据完整'),('context','最终上下文证据完整')]:lines.append(f'| {label} | {summary["legacy"][key]}/18 | {summary["boundary"][key]}/18 |')
for key,label in [('chunks','总块数'),('chunk_ms','纯分块累计毫秒'),('index_ms','索引累计毫秒'),('search_mean_ms','检索均值毫秒'),('rrf_mean_ms','RRF均值毫秒'),('rerank_mean_ms','精排均值毫秒'),('query_mean_ms','检索+RRF+精排均值毫秒')]:lines.append(f'| {label} | {summary["legacy"][key]:.1f} | {summary["boundary"][key]:.1f} |')
lines+=['','## 逐题变化','']
qa={q['case_id']:q for r in rows if r['strategy']=='legacy' for q in r['queries']}
changes=0
for r in rows:
 if r['strategy']!='boundary':continue
 for q in r['queries']:
  old=qa[q['case_id']]
  if any(old[k]['complete']!=q[k]['complete'] for k in ['chunk','candidate','rrf','rerank_top12']) or old['context_complete']!=q['context_complete']:
   changes+=1;lines.append(f'- {q["question"]}：原精排完整={old["rerank_top12"]["complete"]}，新精排完整={q["rerank_top12"]["complete"]}；原上下文完整={old["context_complete"]}，新上下文完整={q["context_complete"]}。')
if not changes:lines.append('这18道题的完整证据命中结果没有变化。')
lines+=['','## 判断与边界','','默认继续使用 legacy。实验策略已经可以配置、预览和异步入库，但本轮不足以证明应全面替换原策略。切分边界更自然不等于召回更准，应在未参与调试的整份文档上重复验证。','','本轮固定FAST解析输出，不能外推为Docling HybridChunker质量对比；没有表格行列准确度或生成答案准确度评测。金标准是已对齐来源的原文原子，仍待完整人工复核。Java回放实际RRF和上下文预算，固定26761可用预算，不含章节扩展、历史对话或聊天生成。','','时间为单轮串行测量，模型预热后运行，含系统负载波动；不能当作并发P95。索引计时包含一次生产分块与向量化、写入、可见性核验；纯分块单独额外测量。所有临时collection均已清理，未替换生产索引。']
lines+=['','## ????','','??????????????RRF????18/18???????????2024??????????????????2??????????????23???????25????Top12?????????????????????????????Reranker??????????????????????','','???????801??866??+8.1%????????1.29???1.92????????+??+???12.56???12.70?????????????????????????????????12.5???????????','','????????17/18??94.4%?????????????????????????????????','']
(out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
