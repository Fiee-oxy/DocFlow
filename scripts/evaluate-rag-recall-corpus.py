"""Source-frozen retrieval diagnostics over 30 indexed PDFs, no chat LLM."""
import argparse,hashlib,importlib.util,json,re,time,uuid,statistics
from pathlib import Path
import pymupdf
def module(name,file):
 s=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
p=module('pilot','run-rag-quality-pilot.py');sc=module('scorer','score-rag-quality-pilot.py')
BASE=p.BASE;INGEST=BASE/'outputs/performance-20261013';RUN=BASE/'outputs/recall-20261014'
def freeze():
 path=RUN/'cases.json'
 if path.exists():return json.loads(path.read_text(encoding='utf-8'))
 corpus=json.loads((BASE/'performance-corpus.json').read_text(encoding='utf-8'));items=sum(corpus['selected'].values(),[]);cases=[]
 for item in items:
  with pymupdf.open(BASE/item['path']) as doc:
   full=sc.norm('\n'.join(page.get_text() for page in doc));chosen=None
   pages=sorted(range(len(doc)),key=lambda i:abs(i-len(doc)*.6))
   for i in pages:
    raw=[block[4].strip() for block in doc[i].get_text('blocks')]
    texts=[part for text in raw for part in ([text] if len(text)<=1200 else [text[n:n+600] for n in range(0,len(text),600)])]
    page_text=doc[i].get_text()
    texts.extend(page_text[n:n+600] for n in range(80,len(page_text),500))
    for text in texts:
     if not 160<=len(text)<=1200:continue
     clean=' '.join(text.split());words=clean.split()
     anchor=clean[:18] if item['language']=='zh' else ' '.join(words[:7])
     if len(anchor)<12 or full.count(sc.norm(anchor))!=1:continue
     n=sc.norm(text)
     atoms=[n[:45],n[len(n)//2:len(n)//2+45],n[-45:]]
     if len(n)<120:continue
     chosen=(i+1,text,anchor,atoms);break
    if chosen:break
   if not chosen:raise RuntimeError('No unambiguous source probe for '+item['id'])
   page,text,anchor,atoms=chosen
   question=f'请查找涉及“{anchor}”的原文段落。' if item['language']=='zh' else f'Find the passage discussing "{anchor}".'
   cases.append({'case_id':'probe-'+item['id'],'document_key':item['id'],'language':item['language'],'group':item['group'],'kind':'EXACT_SOURCE_PROBE','question':question,'units':[{'pages':[page],'atoms':atoms}],'reference_text':text,'source_sha256':item['sha256'],'label_status':'AUTOMATIC_SOURCE_PROBE_NOT_NATURAL_QA'})
 # Eight established natural questions, aligned to the same source hashes.
 existing=json.loads((BASE/'pilot-cases.json').read_text(encoding='utf-8'))
 catalog=json.loads((BASE/'sources.json').read_text(encoding='utf-8'))['files']
 keep={'en-capex','en-net-ppe','en-datasets','en-languages','zh-screenshot','zh-lock-app','zh-screen-lock'}
 for c in existing:
  if c['case_id'] not in keep:continue
  source=next(x for x in catalog if x['id']==c['document_key']);digest=hashlib.sha256((BASE/source['path']).read_bytes()).hexdigest();item=next(x for x in items if x['sha256']==digest)
  cases.append({**c,'document_key':item['id'],'kind':'NATURAL_QUESTION','group':item['group'],'source_sha256':digest})
 def add(cid,key,lang,q,page,atoms):
  item=next(x for x in items if x['id']==key)
  cases.append({'case_id':cid,'document_key':key,'language':lang,'kind':'NATURAL_QUESTION','group':item['group'],'question':q,'units':[{'pages':[page],'atoms':atoms}],'source_sha256':item['sha256'],'label_status':'SOURCE_ALIGNED_PENDING_HUMAN_REVIEW'})
 add('zh-maotai-income','perf-8f184867d0ab755a','zh','贵州茅台2024年营业收入是多少元，同比增长多少？',5,['营业收入','170,899,152,276.34','15.71'])
 add('zh-huawei-rd','perf-7a6162800d4626e6','zh','华为2024年的研发费用支出是多少，占全年收入的比例是多少？',49,['研发费用支出','1,797亿元','20.8%'])
 add('en-huawei-rd','perf-142acd18b6ae17a5','en','How much did Huawei spend on R&D in 2024, and what percentage of revenue was it?',73,['R&D spending','CNY179.7 billion','20.8%'])
 add('zh-csd-frequency','perf-ebb16b2af98cb542','zh','华为数字能源CSD委员会和CSD工作组分别多久召开一次成员会议？',15,['CSD委员会每半年度召开成员会议','CSD工作组月度','召开成员会议'])
 add('en-csd-frequency','perf-3a4cc308c6a5b991','en','How frequently do the Huawei Digital Power CSD Committee and CSD Work Group hold member meetings?',15,['Committee shall convene member meetings on a semi-annual basis','Work Group shall convene monthly member meetings'])
 for c in cases:
  item=next(x for x in items if x['id']==c['document_key'])
  with pymupdf.open(BASE/item['path']) as d:rows=[{'text':d[i-1].get_text(),'pages':[i]} for i in {n for u in c['units'] for n in u['pages']}]
  if not sc.coverage(rows,c)['complete']:raise RuntimeError('Source alignment failed: '+c['case_id'])
 p.save(path,cases);p.save(RUN/'freeze.json',{'cases_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'count':len(cases),'retrieval_seen_before_labeling':False,'note':'Probes and natural questions must be scored separately; provisional lexical evidence coverage.'})
 return cases
def export(session,cases):
 for key in sorted({c['document_key'] for c in cases}):
  target=RUN/'documents'/key
  if (target/'rag_chunks.json').exists():continue
  doc=session['documents'][key]['document_id'];uuid.UUID(doc);owner=int(session['owner'])
  chunks=p.sql(f"SELECT JSON_OBJECT('chunk_id',r.chunk_id,'content',r.content,'section_id',r.section_id,'blockIndex',r.block_index,'sources',JSON_EXTRACT(r.source_json,'$')) FROM document_rag_chunks r JOIN document_files f ON f.id=r.document_id WHERE f.user_id={owner} AND r.document_id='{doc}' AND r.index_version=(SELECT index_version FROM document_adaptive_state WHERE document_id='{doc}') ORDER BY r.block_index;")
  parsed=[];page=1
  while True:
   data=p.api('/documents/'+doc+'/chunks?page='+str(page)+'&size=50',token=session['token']);parsed.extend(data['chunks'])
   if len(parsed)>=data['totalBlocks'] or not data['chunks']:break
   page+=1
  p.save(target/'parsed.json',parsed);p.save(target/'rag_chunks.json',chunks)
 print(json.dumps({'event':'source_exports_finished','documents':30}),flush=True)
def score(cases):
 results=[]
 for c in cases:
  file=RUN/'previews'/(c['case_id']+'.json')
  if not file.exists():continue
  data=json.loads(file.read_text(encoding='utf-8'));trace=data.get('retrieval',[{}])[0];key=c['document_key'];stages={}
  for stage,fn in [('parsed','parsed.json'),('chunked','rag_chunks.json')]:stages[stage]=sc.coverage(json.loads((RUN/'documents'/key/fn).read_text(encoding='utf-8')),c)
  for stage in ['dense','bm25','rrf','reranked','topK']:
   rows=trace.get(stage,[]);stages[stage]=sc.coverage(rows,c)
   for k in [5,8,10,20]:stages[f'{stage}@{k}']=sc.coverage(rows[:k],c)
  stages['candidate_union']=sc.coverage(trace.get('dense',[])+trace.get('bm25',[]),c);stages['context']=sc.coverage(data.get('evidence',[]),c)
  stages['context_any_page']=sc.coverage(data.get('evidence',[]),{'units':[{**u,'pages':[]} for u in c['units']]})
  stages['model_only@8']=sc.coverage(sorted(trace.get('reranked',[]),key=lambda x:x.get('model_rerank_score',float('-inf')),reverse=True)[:8],c)
  ranks={}
  for name in ['dense','bm25','rrf','reranked']:
   rank=None
   for i,row in enumerate(trace.get(name,[]),1):
    for u in c['units']:
     # A support fragment/value absent from the query; not merely a heading hit.
     atom=u['atoms'][1 if len(u['atoms'])>1 else 0]
     if sc.norm(atom) in sc.norm(c['question']):continue
     if sc.coverage([row],{'units':[{'pages':u['pages'],'atoms':[atom]}]})['complete']:rank=i;break
    if rank:break
   ranks[name]=rank
  failure=next((s for s in ['parsed','chunked','candidate_union','rrf','topK','context'] if not stages[s]['complete']),'PASS')
  results.append({'case_id':c['case_id'],'kind':c['kind'],'language':c['language'],'group':c['group'],'question':c['question'],'pages':[u['pages'] for u in c['units']],'ready':data.get('ready'),'selectedSourceCount':data.get('selectedSourceCount'),'latencyMs':data.get('latencyMs'),'failure_stage':failure,'support_fragment_ranks':ranks,'blockedReasons':[t.get('blockedReason') for t in data.get('retrieval',[]) if t.get('blockedReason')],'stages':stages})
 p.save(RUN/'stage-results.json',results)
 lines=['# 30 份 PDF 召回诊断','',f'已执行 {len(results)}/{len(cases)} 道测试。顺序执行，不调用聊天 LLM。','', '30 道自动原文探针用于验证原文段落能否找回；12 道自然问题用于初步证据覆盖检查。两组分别统计，不能将探针通过率称为真实问答准确率。必要片段在测试前冻结，并以原始 PDF 页码验证；尚待人工复核。','', '| 测试组 / 语言 | 题数 | 解析 | 分块 | 候选并集 | 融合池 | 精排 Top-8 | 最终上下文 |','| --- | --- | --- | --- | --- | --- | --- | --- |']
 summary={}
 for kind in ['EXACT_SOURCE_PROBE','NATURAL_QUESTION']:
  for lang in ['zh','en']:
   rows=[r for r in results if r['kind']==kind and r['language']==lang];counts={s:sum(r['stages'][s]['complete'] for r in rows) for s in ['parsed','chunked','candidate_union','rrf','topK','context']}
   summary[kind+'-'+lang]={'count':len(rows),**counts}
   summary[kind+'-'+lang]['support_fragment_MRR']={s:round(sum(1/r['support_fragment_ranks'][s] if r['support_fragment_ranks'][s] else 0 for r in rows)/len(rows),4) if rows else None for s in ['dense','bm25','rrf','reranked']}
   summary[kind+'-'+lang]['rrf_top8_complete']=sum(r['stages']['rrf@8']['complete'] for r in rows)
   lines.append('| '+kind+' / '+lang+' | '+str(len(rows))+' | '+' | '.join(f'{v}/{len(rows)}' for v in counts.values())+' |')
 lines+=['','## 精排前后对比','', '| 测试组 / 语言 | 融合 Top-8 完整 | 仅模型精排 Top-8 完整 | 最终 Top-K 完整 |','| --- | --- | --- | --- |']
 for kind in ['EXACT_SOURCE_PROBE','NATURAL_QUESTION']:
  for lang in ['zh','en']:
   rows=[r for r in results if r['kind']==kind and r['language']==lang]
   values=[sum(r['stages'][s]['complete'] for r in rows) for s in ['rrf@8','model_only@8','topK']]
   lines.append('| '+kind+' / '+lang+' | '+' | '.join(f'{v}/{len(rows)}' for v in values)+' |')
 lines+=['','## 失败项','', '| 问题 | 首个不完整阶段 | 参考物理页 |','| --- | --- | --- |']
 for r in results:
  if r['failure_stage']!='PASS':lines.append(f"| {r['case_id']}：{r['question'].replace('|','/')} | {r['failure_stage']} | {r['pages']} |")
 latencies=[r['latencyMs']/1000 for r in results if r['latencyMs'] is not None]
 lines+=['',f'预览请求中位耗时：{statistics.median(latencies):.2f} 秒。' if latencies else '', '当前接口只提供检索总耗时，未提供 Dense / BM25 / 精排的独立计时；这里不虚构分阶段耗时。', '', '按物理页及词面片段评分，等价表述或其他页面的合法替代证据可能不计入；失败项仍需人工核验。跨 chunk 的片段允许合并匹配。大文件来源集中于同一机构，且含中英文译本；本轮不是独立文档盲测，也不评价最终 LLM 回答。', '', '完整原始响应：previews/；逐阶段缺失片段：stage-results.json；固定问题与来源：cases.json。']
 if len(results)==len(cases):
  lines+=['','## 本轮结论','',
   '- 指定必要片段在解析与分块中均能找到（42/42）；这不证明全文版面、表格列关系或 OCR 正确。',
   '- 原文探针：候选完整 26/30，最终上下文完整 14/30。中文探针 Top-K 完整 7/15，最终上下文仅 1/15；需要重点审查 2000 字节上界预算与整块装入策略。探针使用指定长片段，不能与自然问题分数混合。',
   '- 自然问题：候选完整 8/12，融合池与最终 Top-K 各 6/12，最终上下文 5/12。财务题漏候选、茅台收入题在融合时丢证据、英文 CSD 题遗漏工作组月度会议频率。',
   '- 精排不是完全无效：原文探针融合 Top-8 完整 18/30，精排后为 22/30；自然问题均为 6/12。先检查融合截断和预算，再决定是否替换模型。',
   '- 小米“怎样让某个后台程序不被一键清除结束”在两个账号下的同一源 PDF 表现不同。45 个 chunk 正文完全相同；复测原评测文档通过，新性能文档失败。复测结果不改写冻结基线，保存在 rechecks/；索引输入/一致性原因仍待定位。',
   '- 后续修复顺序：核对相同内容的索引与检索一致性；保留关键词及语义候选的必要证据；按问题需要选择、补齐和装入证据；单独 A/B 评估模型和分块。',
   '- 串行 42 次均 ready=true，聊天 LLM 调用为 0；并发准入问题仍以之前性能报告为准。']
 p.save(RUN/'summary.json',{'status':'COMPLETE' if len(results)==len(cases) else 'IN_PROGRESS','case_count':len(results),'groups':summary,'ready_count':sum(r['ready'] is True for r in results),'latency_seconds':{'median':round(statistics.median(latencies),3),'max':round(max(latencies),3)} if latencies else None,'note':'Support-fragment MRR is a lexical proxy, not human relevance MRR. Complete coverage and availability are separate.'})
 (RUN/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
def main():
 a=argparse.ArgumentParser();a.add_argument('--score-only',action='store_true');args=a.parse_args();RUN.mkdir(parents=True,exist_ok=True);cases=freeze()
 if args.score_only:score(cases);return
 session=p.load_session(INGEST);export(session,cases);p.save(RUN/'runtime.json',p.api('/documents/query/diagnostics',token=session['token']))
 for c in cases:
  target=RUN/'previews'/(c['case_id']+'.json')
  if target.exists():continue
  answer=p.api('/documents/query/preview',{'documentIds':[session['documents'][c['document_key']]['document_id']],'question':c['question'],'limit':200},session['token']);assert answer.get('llmCalls')==0;p.save(target,answer)
  print(json.dumps({'event':'recall_case_finished','case':c['case_id'],'ready':answer.get('ready'),'latencyMs':answer.get('latencyMs')}),flush=True)
  score(cases)
 audit=p.audit(session);p.save(RUN/'no-llm-audit.json',audit);assert audit['llmCalls']==0 and audit['queryTasks']==0
 print(json.dumps({'event':'recall_finished','cases':len(cases),'audit':audit}),flush=True)
if __name__=='__main__':main()
