"""Freeze additional source units and paraphrases before running either strategy."""
import json,re,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'evaluation/chunk-expanded-20261019'
OUT.mkdir(exist_ok=True)
def read(path):return json.loads(path.read_text(encoding='utf-8'))
quality=ROOT/'evaluation/rag-quality/outputs'
natural=read(quality/'natural-90-20261014/cases.json')
holdout=read(quality/'completion-holdout-20261014/cases.json')
original=read(ROOT/'evaluation/parser-ab-20261018/manifest.json')
variants={
 'perf-2cbcdc1cd99a7c58-q1':'How much did 3M spend on capital investments during 2018? Give the amount in millions of US dollars.',
 'perf-2cbcdc1cd99a7c58-q2':'At the end of 2018, what was the net carrying amount of 3M property, plant and equipment, expressed in USD millions?',
 'perf-2cbcdc1cd99a7c58-q3':'On what date did 3M stop accepting new participants into its main US defined-benefit pension plan?',
 'perf-8f184867d0ab755a-q1':'请给出茅台2024年的营收金额（元）和相较上年的增幅。',
 'perf-8f184867d0ab755a-q2':'茅台2024年有多少名研发人员？他们占全部员工的百分比是多少？',
 'perf-8f184867d0ab755a-q3':'年报怎样解释茅台2024年营收变化的原因？',
 'pbc-2024q4-q1':'2024年实施了几轮降准？累计下调幅度和释放的长期资金规模各是多少？',
 'pbc-2024q4-q2':'截至2024年底，社融存量与广义货币M2各比上年增长了多少？',
 'pbc-2024q4-q3':'2024年12月份，企业新贷款和个人住房新贷款的加权平均利率各是多少？',
 'rfc9110-q1':'According to the HTTP semantics standard, list all request methods classified as safe.',
 'rfc9110-q2':'Explain the meaning of idempotence for HTTP requests and identify the methods that satisfy it.',
 'rfc9110-q3':'In which two formats may a server express the value of Retry-After?'
}
docs=[]
for key,folder,group in [
 ('perf-2cbcdc1cd99a7c58','recall-20261014','ADDITIONAL_FINANCIAL'),
 ('perf-8f184867d0ab755a','recall-20261014','ADDITIONAL_FINANCIAL'),
 ('pbc-2024q4','completion-holdout-20261014','DOCUMENT_HOLDOUT'),
 ('rfc9110','completion-holdout-20261014','DOCUMENT_HOLDOUT')]:
 parsed=quality/folder/'documents'/key/'parsed.json'
 blocks=read(parsed);batches={}
 for block in blocks:
  for s in block['sources']:
   ref=s.get('ref','');match=re.match(r'b(\d+):',ref);n=int(match[1]) if match else 0
   batches.setdefault(n,[]).append({'text':s.get('text',block['text']),'section':s.get('title_path',block['section']),
       'pages':s.get('pages',[]),'ref':ref.split(':',1)[-1],'label':s.get('label','text')})
 events=[{'type':'batch','blocks':v} for _,v in sorted(batches.items())]
 cases=[dict(c,group=group,variant='ORIGINAL',base_case_id=c['case_id']) for c in natural+holdout if c['document_key']==key]
 cases += [dict(c,case_id=c['case_id']+'-paraphrase',question=variants[c['case_id']],variant='PARAPHRASE') for c in cases if c['case_id'] in variants]
 docs.append({'id':key,'language':cases[0]['language'],'group':group,'input_kind':'SAVED_TEXT_UNITS',
     'pages':max(p for b in blocks for s in b['sources'] for p in s.get('pages',[])),
     'source_sha256':cases[0]['source_sha256'],'cases':cases})
 target=OUT/'inputs'/key;target.mkdir(parents=True,exist_ok=True)
 (target/'events.jsonl').write_text('\n'.join(json.dumps(e,ensure_ascii=False) for e in events),encoding='utf-8')
for key in ['perf-c6e7e73466012702','perf-5a023e1dce750ab2']:
 d=next(d for d in original['documents'] if d['id']==key)
 target=OUT/'inputs'/key;target.mkdir(parents=True,exist_ok=True)
 source=ROOT/'evaluation/parser-ab-20261018/results'/key/'legacy_docling/events.jsonl'
 (target/'events.jsonl').write_bytes(source.read_bytes())
 docs.append({'id':key,'language':d['language'],'pages':d['pages'],'group':'DOCLING_INPUT','input_kind':'DOCLING_DOCUMENT',
     'source_sha256':d['sha256'],'cases':[dict(c,group='DOCLING_INPUT',variant='ORIGINAL',base_case_id=c['case_id']) for c in d['cases']]})
for d in docs:d['events_sha256']=hashlib.sha256((OUT/'inputs'/d['id']/'events.jsonl').read_bytes()).hexdigest()
(OUT/'manifest.json').write_text(json.dumps({'documents':docs,'llmCalls':0,'note':'Held-out from chunk-strategy tuning, not previously unseen in all historical project evaluations.'},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'documents':len(docs),'original_questions':sum(c['variant']=='ORIGINAL' for d in docs for c in d['cases']),
    'paraphrases':sum(c['variant']=='PARAPHRASE' for d in docs for c in d['cases'])}))
