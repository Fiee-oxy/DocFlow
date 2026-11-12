"""Summarize measured end-to-end timings; never substitutes missing runs."""
import argparse,json,math,statistics
from pathlib import Path
a=argparse.ArgumentParser();a.add_argument('--run-dir',required=True);args=a.parse_args();run=Path(args.run_dir)
b=Path('evaluation/rag-quality');corpus=json.loads((b/'performance-corpus.json').read_text(encoding='utf-8'));items=sum(corpus['selected'].values(),[])
data=json.loads((run/'performance-results.json').read_text(encoding='utf-8'));results=data['results']
def stats(values):
 return {'n':len(values),'median':round(statistics.median(values),3),'p95':round(sorted(values)[math.ceil(len(values)*.95)-1],3),'max':round(max(values),3)} if values else None
summary={'status':'COMPLETE' if all(r['id'] in results and results[r['id']]['state']!='RUNNING' for r in items) else 'IN_PROGRESS','target_documents':30,'groups':{},'extra_samples':len(set(results)-{r['id'] for r in items})}
summary.update(total_bytes=sum(i['bytes'] for i in items),total_pages=sum(i['pages'] for i in items),indexed_chunks=sum(results.get(i['id'],{}).get('indexed_chunks',0) or 0 for i in items),page_counts_match=all(results.get(i['id'],{}).get('parsed_pages')==i['pages'] for i in items))
lines=['# PDF 性能测试实测记录','',f"状态：{summary['status']}。目标样本 10 个小 PDF、20 个大 PDF；中英文各 15 个。",'',
 f"共 {summary['total_bytes']/1e6:.2f} MB、{summary['total_pages']} 页，当前已发布 {summary['indexed_chunks']} 个 RAG chunk。页数核对通过不代表逐页内容准确。",
 '上传走本机 loopback；最多同时有 2 份文档处于入库流程。PDF 使用当前默认 FAST，索引使用本地 BGE / Milvus，不调用聊天 LLM。为了比较完整 RAG 入库，小文件解析完成后也显式申请建索引；这不是默认小文件直读路由的耗时。',
 '每份耗时从开始上传计至对应阶段，包含流水线内等待；没有把下载等待算进单文档耗时。首批索引时间是轮询首次观察到 indexedChunks>0，不等于已执行提问验证。轮询间隔约 2 秒，另有上传/API 调用开销，阶段耗时为观察上界，不具备毫秒级精度。',
 '', '| 分组 | 完成 / 目标 | 体积范围 MB | 页数范围 | 上传中位数 s | 解析完成中位数 s | 首批索引中位数 s | 完整索引中位数 / P95 s |','| --- | --- | --- | --- | --- | --- | --- | --- |']
for group in ['small','large']:
 docs=corpus['selected'][group];measured=[results[r['id']] for r in docs if r['id'] in results];ready=[r for r in measured if r['state']=='READY']
 g={'count':len(docs),'ready':len(ready),'failed':sum(r['state'] in ['FAILED','UPLOAD_FAILED','TIMEOUT'] for r in measured),'bytes_range':[min(r['bytes'] for r in docs),max(r['bytes'] for r in docs)],'pages_range':[min(r['pages'] for r in docs),max(r['pages'] for r in docs)]}
 for metric in ['upload_seconds','parse_complete_seconds','first_indexed_seconds','index_complete_seconds']:g[metric]=stats([r[metric] for r in ready if metric in r])
 summary['groups'][group]=g
 def med(k):return g[k]['median'] if g[k] else '—'
 tail=f"{med('index_complete_seconds')} / {g['index_complete_seconds']['p95']}" if g['index_complete_seconds'] else '—'
 lines.append(f"| {group} | {len(ready)}/{len(docs)} | {g['bytes_range'][0]/1e6:.2f}–{g['bytes_range'][1]/1e6:.2f} | {g['pages_range'][0]}–{g['pages_range'][1]} | {med('upload_seconds')} | {med('parse_complete_seconds')} | {med('first_indexed_seconds')} | {tail} |")
audit=run/'no-llm-audit.json'
intervals=sorted((results[i['id']]['started_at'],results[i['id']]['started_at']+results[i['id']]['index_complete_seconds']) for i in items if results.get(i['id'],{}).get('state')=='READY')
merged=[]
for start,end in intervals:
 if merged and start<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],end)
 else:merged.append([start,end])
active_seconds=sum(end-start for start,end in merged)
if active_seconds:
 summary['completed_target_active_window_seconds']=round(active_seconds,3)
 summary['completed_target_docs_per_active_minute']=round(len(intervals)*60/active_seconds,3)
 lines+=['',f"已完成正式样本的流水线活动区间合并计 {active_seconds:.1f} 秒，约 {summary['completed_target_docs_per_active_minute']} 份/活动分钟。排除无正式样本处理的空闲区间，但保留区间内资源竞争；不是持续满负载容量上限。"]
if audit.exists():summary['chat_llm_audit']=json.loads(audit.read_text(encoding='utf-8'))
samples=run/'resource-samples.jsonl'
if samples.exists():
 rows=[json.loads(s) for s in samples.read_text(encoding='utf-8').splitlines() if s.strip()]
 if rows:
  summary['host_resources']={'samples':len(rows),'cpu_percent':stats([r['host_cpu_percent'] for r in rows]),'peak_used_memory_GiB':round(max(r['host_memory_used_bytes'] for r in rows)/2**30,2)}
  lines+=['',f"主机资源采样：CPU 中位数 {summary['host_resources']['cpu_percent']['median']}%，主机总已用内存峰值 {summary['host_resources']['peak_used_memory_GiB']} GiB。包含其他应用，不能全部归因于本项目；采样开始晚于首批预热。"]
retrieval=run/'retrieval-performance.json'
if retrieval.exists():
 summary['retrieval']=json.loads(retrieval.read_text(encoding='utf-8'));lines+=['','## 检索负载','', '| 并发 | 请求数 | 批次耗时 s | 可用请求中位数 / P95 s | HTTP 失败 | HTTP 成功但 ready=false | 可用结果 |','| --- | --- | --- | --- | --- | --- | --- |']
 for batch in summary['retrieval']['measurements']:
  good=[r for r in batch['requests'] if not r['error'] and r.get('ready') is True];s=stats([r['seconds'] for r in good]);latency=f"{s['median']} / {s['p95']}" if s else '—';lines.append(f"| {batch['concurrency']} | {len(batch['requests'])} | {batch['batch_seconds']} | {latency} | {sum(bool(r['error']) for r in batch['requests'])} | {sum(not r['error'] and r.get('ready') is False for r in batch['requests'])} | {len(good)}/{len(batch['requests'])} |")
 lines+=['','延迟只统计 HTTP 成功且 ready=true 的请求；这也不代表证据正确。快速拒绝和 ready=false 都不会降低可用请求延迟。并发 4 的 4 次 HTTP 失败均为 HTTP 409“检索预览繁忙”；AdaptiveQueryService 的 previewSlots 为 Semaphore(2)，且使用 tryAcquire 立即拒绝。因此这一组测到的是接口准入上限，不能据此推断模型 4 并发能力。',
  '', '同一批代表文档依次测试并发 1/2/4，每组仅 6 次请求，存在预热和缓存顺序影响；请求未人工标注，因此这些结果只代表耗时，不代表召回准确率。',
  '', '并发 2 另做 6 次复测，仍只有 3 次 ready=true。完整预览确认阻塞原因为“本地检索服务暂不可用（HTTP 429）”。services/retrieval/app.py 的门控是 BoundedSemaphore(1)，最多等 1 秒就返回 429；这是本地 BGE 服务的准入冲突，与第三方聊天模型限流无关。复测保存在 retrieval-validation.json 和 retrieval-previews/。',
  '', '下一步优先：协调 Java 预览与 Python 模型服务的准入、队列和超时，并将并发数配置化；区分索引 Embedding 与交互检索，避免争用同一门控。随后单独 A/B 验证批处理与并发。不要直接把线程数调大后宣称吞吐提升。']
lines+=['','## 每份文件','', '| ID | 语言 | MB | 页数 | 状态 | 解析 s | 完整索引 s |','| --- | --- | --- | --- | --- | --- | --- |']
for item in items:
 r=results.get(item['id'],{});lines.append(f"| {item['id']} | {item['language']} | {item['bytes']/1e6:.2f} | {item['pages']} | {r.get('state','NOT_STARTED')} | {r.get('parse_complete_seconds','—')} | {r.get('index_complete_seconds','—')} |")
lines+=['','## 范围说明','',
 '- 小文件包含论文、手册、财务及 ESG 文档；大文件目前集中于华为年报、可持续发展报告及画册，具有明显来源偏差，不能作为跨行业质量结论。中英文译本不是独立语义样本。',
 '- 文件字节大小、页数和文本量分开记录；图片较多的大文件不一定产生更多 chunk。',
 '- 先前入队、后来未纳入最终清单的文件保留为预热/异常样本，单独统计，不混入上表的 30 份结果。',
 '- 无文本层的异常样本已返回“文档没有可检索的文本内容”；未开启 OCR，不把它当作正常文本 PDF 成功。',
 '- 当前只测默认配置的一次入库，并发数 2；不是 8 核远程服务器基准，也不是完整的并发扩展曲线。',
 '- 明细：performance-results.json；数据来源与 SHA256：../../performance-corpus.json；资源：resource-samples.jsonl；配置：runtime.json / services.json / host.json。',
 '- .session.json 含账号凭据，不应共享。']
(run/'performance-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');(run/'performance-report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps(summary,ensure_ascii=True))
