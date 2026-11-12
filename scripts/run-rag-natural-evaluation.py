"""Execute 90 frozen natural questions against existing indexes; no chat LLM."""
import argparse,hashlib,importlib.util,json,statistics,time,math,collections
from pathlib import Path
def module(name,file):
 spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
p=module('pilot','run-rag-quality-pilot.py');sc=module('sc','score-rag-quality-pilot.py');source_coverage=module('source_coverage','rag-coverage.py')
BASE=p.BASE;RUN=BASE/'outputs/natural-90-20261014';INGEST=BASE/'outputs/performance-20261013';EXPORTS=BASE/'outputs/recall-20261014/documents'
TITLES={'perf-5a0063cbe381a1f9':'pioNER: Armenian Named Entity Recognition','perf-a73aca5941761ea9':'小米手机2用户手册','perf-c647ae68c83aea86':'华为2019 ICT可持续发展目标标杆报告（中文）','perf-b1f4fbda7214b999':'Huawei 2019 ICT SDG Benchmark Report','perf-785bb14c5823cfe5':'华为2018年可持续发展报告（中文）','perf-e50fb54a86ae5c36':'Huawei 2018 Sustainability Report','perf-67ab5de4a1c922f8':'Huawei 2019 Sustainability Report'}
def title(c):return TITLES.get(c['document_key'],c['title'])
def assess(case,raw,cache):
 trace=raw.get('retrieval',[{}])[0];stages={}
 lookup={row['chunk_id']:row.get('blockIndex',row.get('block_index',i)) for i,row in enumerate(cache[case['document_key']]['rag_chunks.json'])}
 for stage,file in [('parsed','parsed.json'),('chunked','rag_chunks.json')]:stages[stage]=sc.coverage(cache[case['document_key']][file],case)
 for name in ['dense','bm25','rrf','reranked','topK']:
  stages[name]=source_coverage.coverage(trace.get(name,[]),case,lookup)
  for k in [5,8,10,20]:stages[f'{name}@{k}']=source_coverage.coverage(trace.get(name,[])[:k],case,lookup)
 stages['candidate_union']=source_coverage.coverage(trace.get('dense',[])+trace.get('bm25',[]),case,lookup)
 stages['context']=source_coverage.coverage(raw.get('evidence',[]),case,lookup)
 stages['context_any_page']=source_coverage.coverage(raw.get('evidence',[]),{'units':[{**u,'pages':[]} for u in case['units']]},lookup)
 failure='PASS' if stages['context']['complete'] else next((s for s in ['parsed','chunked','candidate_union','rrf','topK','context'] if not stages[s]['complete']),'PASS')
 if raw.get('evaluation_error') or raw.get('ready') is False:failure='service'
 return {'case_id':case['case_id'],'document_key':case['document_key'],'title':case['title'],'language':case['language'],'group':case['group'],'category':case['kind'],'question':case['question'],'ready':raw.get('ready'),'latencyMs':raw.get('latencyMs'),'selectedSourceCount':raw.get('selectedSourceCount'),'first_incomplete_stage':failure,'stages':stages,'blockedReasons':[t.get('blockedReason') for t in raw.get('retrieval',[]) if t.get('blockedReason')],'evaluation_error':raw.get('evaluation_error'),'evidenceBudget':raw.get('evidenceBudget'),'omittedReasons':dict(collections.Counter(x.get('reason') for x in raw.get('omittedEvidence',[]))),'selected_sources':[{'id':r.get('id'),'pages':r.get('pages',[]),'blockIndex':r.get('blockIndex'),'excerpt':' '.join(r.get('text','').split())[:240]} for r in raw.get('evidence',[])]}
def report(cases,results):
 rows=list(results.values());groups={}
 for lang in ['zh','en']:
  subset=[r for r in rows if r['language']==lang]
  groups[lang]={'tested':len(subset),**{stage:sum(r['stages'][stage]['complete'] for r in subset) for stage in ['parsed','chunked','candidate_union','rrf','topK','context']}}
 times=[r['latencyMs']/1000 for r in rows if r['latencyMs'] is not None]
 failures=dict(collections.Counter(r['first_incomplete_stage'] for r in rows));summary={'status':'COMPLETE' if len(rows)==len(cases) else 'IN_PROGRESS','tested':len(rows),'planned':len(cases),'groups':groups,'first_incomplete_stages':failures,'ready':sum(r['ready'] is True for r in rows),'median_seconds':round(statistics.median(times),3) if times else None,'p95_seconds':round(sorted(times)[math.ceil(.95*len(times))-1],3) if times else None}
 p.save(RUN/'summary.json',summary);p.save(RUN/'stage-results.json',rows)
 complete=sum(r['stages']['context']['complete'] for r in rows)
 review_path=RUN/'review-overrides.json';reviews=json.loads(review_path.read_text(encoding='utf-8'))['reviews'] if review_path.exists() else []
 # An old review cannot blindly approve a new context for the same question.
 reviewed_valid=set()
 for review in reviews:
  path=RUN/'previews'/(review['case_id']+'.json')
  if review['decision']!='VALID_ALTERNATIVE_CONTEXT' or not path.exists():continue
  evidence=json.loads(path.read_text(encoding='utf-8')).get('evidence',[])
  required=review.get('required_atoms',[review['retrieved']])
  if any(review.get('retrieved_page') in source.get('pages',[]) and all(sc.norm(atom) in sc.norm(source.get('text','')) for atom in required) for source in evidence):
   reviewed_valid.add(review['case_id'])
 newly_valid=[r for r in rows if r['case_id'] in reviewed_valid and not r['stages']['context']['complete']]
 summary['reviewed_context_complete']=complete+len(newly_valid);summary['additional_valid_alternative_contexts']=len(newly_valid);summary['review_status']='Two specific unit-conversion cases reviewed by assistant; not all cases independently adjudicated.'
 reviewed_failures=dict(collections.Counter('PASS' if r['case_id'] in reviewed_valid else r['first_incomplete_stage'] for r in rows));summary['reviewed_first_incomplete_stages']=reviewed_failures
 p.save(RUN/'summary.json',summary)
 lines=['# 每份文档三道自然问题：测试分析','',f"状态：{summary['status']}，已执行 {len(rows)}/90 道。30 份已入库 PDF，每份 3 道，中英文各 45 道。",'',
  f'当前可确认 {complete+len(newly_valid)}/{len(rows)} 道最终材料完整：自动词面判定 {complete} 道，另有 {len(newly_valid)} 道单位换算误判经助手复核后确认有效。这表示模型有机会拿到所需材料，不等于它一定回答正确；其余标注尚未独立人工复核。',
  '本轮不调用聊天 LLM。问题由助手根据原始 PDF 编写，参考片段在检索前冻结；未经独立人工复核，是诊断集，不是正式盲测。没有把原文定位探针混进自然问题分数。',
  '', '## 结果怎么理解','',
  '- 解析、分块：指定答案片段是否还存在。通过不等于整份文件版面或表格均正确。',
  '- 候选：搜索是否找齐全部必要片段。缺在这里，精排和聊天模型都无法补救。',
  '- 融合、Top-8：已找到的片段是否被保留下来。这里下降说明候选取舍或排序需要检查。',
  '- 最终上下文：真正会交给回答模型的证据是否齐全。金额和比例等多项问题，缺一项就不算完整。',
  '', '| 语言 | 已测 | 解析完整 | 分块完整 | 候选完整 | 融合池完整 | Top-8 完整 | 最终上下文完整 |','| --- | --- | --- | --- | --- | --- | --- | --- |']
 for lang,g in groups.items():lines.append('| '+('中文' if lang=='zh' else '英文')+' | '+str(g['tested'])+' | '+' | '.join(f"{g[s]}/{g['tested']}" for s in ['parsed','chunked','candidate_union','rrf','topK','context'])+' |')
 labels={'parsed':'解析中找不到指定证据','chunked':'分块中找不到指定证据','candidate_union':'候选召回不完整','rrf':'融合池丢失必要证据','topK':'最终 Top-K 未保留全部证据','context':'最终上下文未装入全部证据','service':'服务不可用，不能据此判定召回质量','PASS':'完整保留证据'}
 lines+=['','## 优先处理什么','']
 lines+=['以下分类排除已确认的两道单位换算误判；其余标注尚未独立复核。','']
 for k,count in sorted(reviewed_failures.items(),key=lambda kv:kv[1],reverse=True):lines.append(f'- {labels[k]}：{count} 道。')
 lines+=['','缺失阶段表示“第一次观察到不完整”，不是已经证明的唯一根因。先修影响题数最多的环节，再用同一冻结问题集做 A/B，不在测试过程中改参数。',
  '', '## 评分误判复核','',
  f'自动词面评分完整 {complete}/{len(rows)}。另有 {len(newly_valid)} 道已确认存在合法替代证据，合计可确认 {complete+len(newly_valid)}/{len(rows)} 道材料完整；其余标注仍待独立人工复核。上面的分阶段表保留原始词面分数，不改写冻结标签。',
  '- 华为2023年英文收入题：最终上下文为 CNY704,174 million，即 CNY704.174 billion，与标注的 CNY704.2 billion 四舍五入一致。',
  '- 华为2025年中文销售收入题：最终上下文为 880,941 百万元，即 8,809.41 亿元，与致辞中约 8,809 亿元一致。',
  '两题是评分规则漏掉单位换算与有效数字，不应作为检索错误。复核记录见 review-overrides.json；这是助手复核，不是独立人工验收。',
  '', '## 具体失败例子','']
 shown=collections.Counter()
 for r in rows:
  stage=r['first_incomplete_stage']
  if stage=='PASS' or r['case_id'] in reviewed_valid or shown[stage]>=2:continue
  shown[stage]+=1
  missing=[a for u in r['stages']['context']['units'] for a in u['missing']]
  lines+= [f"- **{title(r)}**：{r['question']} 首次不完整在“{labels[stage]}”；最终缺失片段/数值：{'；'.join(missing)}。"]
 lines+=['','## 每份文档的结果','', '候选列为原始词面覆盖；最终列包含两道已确认的单位换算复核。','', '| 文档 | 语言 | 已测 | 候选词面完整 | 最终证据完整 |','| --- | --- | --- | --- | --- |']
 for key in dict.fromkeys(c['document_key'] for c in cases):
  subset=[r for r in rows if r['document_key']==key];c=next(c for c in cases if c['document_key']==key)
  lines.append(f"| {title(c).replace('|','/')} | {c['language']} | {len(subset)}/3 | {sum(r['stages']['candidate_union']['complete'] for r in subset)}/{len(subset)} | {sum(r['stages']['context']['complete'] or r['case_id'] in reviewed_valid for r in subset)}/{len(subset)} |")
 lines+=['',f"检索请求耗时中位数 {summary['median_seconds']} 秒，P95 {summary['p95_seconds']} 秒。这里只提供整体检索耗时，接口尚未暴露各检索阶段的独立耗时。",'',
  '每份只有 3 道题，不能据此给文件或模型作稳定的准确率排名。大文件集中于同一机构，包含译本；题目包含回归题且未做独立人工复核。较多问题来自公司概况、致辞等靠前章节，正文中后段与跨页关联覆盖不足。本轮没有覆盖无答案题、完整全文概览和全部复杂表格。数字词面齐全也不证明表格年份/列关系一定正确。只有冻结同一问题和来源的前后对照可用于分析改动效果，不能与不同题集直接比较。',
  '', '全部问题与来源页码：questions.md；原始响应：previews/；逐阶段缺失片段：stage-results.json；固定标注：cases.json；零聊天调用核验：no-llm-audit.json。']
 (RUN/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
 qlines=['# 固定自然问题及必要证据','', '以下标注在本轮查询前固定；参考物理页允许原文中已核实的等价位置。','']
 for c in cases:
  r=results.get(c['case_id']);source=(BASE/'performance-documents'/(c['document_key'].removeprefix('perf-')+'.pdf')).resolve().as_posix();qlines +=[f"## {title(c)} · {c['case_id']}",'',c['question'],'',f"[查看源 PDF]({source})；参考物理页：{c['units'][0]['pages']}。必要片段/数值：{'；'.join(c['units'][0]['atoms'])}。",'',f"结果：{labels[r['first_incomplete_stage']] if r else '未执行'}。",'']
  if r:
   missing=[a for u in r['stages']['context']['units'] for a in u['missing']]
   if c['case_id'] in reviewed_valid:qlines +=['复核：最终上下文包含单位换算后的合法替代证据，本题不应作为检索失败；原始词面评分保留。','']
   if missing:qlines +=['词面检查未匹配：'+'；'.join(missing)+'。','']
   for selected in r.get('selected_sources',[]):qlines +=[f"实际选入来源，物理页 {selected['pages']}（节选）：",'', '> '+selected['excerpt'],'']
 (RUN/'questions.md').write_text('\n'.join(qlines),encoding='utf-8')
def main():
 global RUN
 a=argparse.ArgumentParser();a.add_argument('--score-only',action='store_true');a.add_argument('--run-dir');args=a.parse_args()
 if args.run_dir:
  target=Path(args.run_dir).resolve()
  if (BASE/'outputs').resolve() not in target.parents:a.error('Run must be inside evaluation outputs')
  target.mkdir(parents=True,exist_ok=True)
  for filename in ['cases.json','freeze.json','review-overrides.json']:
   if not (target/filename).exists():(target/filename).write_bytes((RUN/filename).read_bytes())
  RUN=target
 cases=json.loads((RUN/'cases.json').read_text(encoding='utf-8'));freeze=json.loads((RUN/'freeze.json').read_text(encoding='utf-8'))
 assert hashlib.sha256((RUN/'cases.json').read_bytes()).hexdigest()==freeze['sha256']
 cache={key:{f:json.loads((EXPORTS/key/f).read_text(encoding='utf-8')) for f in ['parsed.json','rag_chunks.json']} for key in {c['document_key'] for c in cases}}
 results={}
 if not args.score_only:
  session=p.load_session(INGEST);states={}
  for key in cache:
   assert session['documents'][key]['sha256']==next(c['source_sha256'] for c in cases if c['document_key']==key)
   state=p.api('/documents/'+session['documents'][key]['document_id']+'/strategy',token=session['token']);assert state['state']=='READY';states[key]=state
  p.save(RUN/'index-states.json',states);p.save(RUN/'runtime.json',p.api('/documents/query/diagnostics',token=session['token']))
 for c in cases:
  target=RUN/'previews'/(c['case_id']+'.json')
  if target.exists():raw=json.loads(target.read_text(encoding='utf-8'))
  elif args.score_only:continue
  else:
   try:
    raw=p.api('/documents/query/preview',{'documentIds':[session['documents'][c['document_key']]['document_id']],'question':c['question'],'limit':200},session['token']);assert raw.get('llmCalls')==0
   except Exception as error:raw={'evaluation_error':str(error),'llmCalls':0}
   p.save(target,raw)
  results[c['case_id']]=assess(c,raw,cache)
  if not args.score_only:print(json.dumps({'event':'natural_case_finished','finished':len(results),'case':c['case_id'],'ready':raw.get('ready'),'first_incomplete':results[c['case_id']]['first_incomplete_stage']}),flush=True)
  if not args.score_only:report(cases,results)
 if args.score_only:report(cases,results)
 if not args.score_only:
  audit=p.audit(session);p.save(RUN/'no-llm-audit.json',audit);assert audit['llmCalls']==0 and audit['queryTasks']==0
  print(json.dumps({'event':'natural_evaluation_finished','tested':len(results),'audit':audit}),flush=True)
if __name__=='__main__':main()
