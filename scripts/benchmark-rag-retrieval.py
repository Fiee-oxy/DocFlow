"""Measure preview retrieval at concurrency 1/2/4 without answer generation."""
import argparse,concurrent.futures,importlib.util,json,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('pilot',Path(__file__).with_name('run-rag-quality-pilot.py'));p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
def main():
 a=argparse.ArgumentParser();a.add_argument('--run-dir',required=True);a.add_argument('--concurrencies',type=int,nargs='+',default=[1,2,4]);a.add_argument('--output-file',default='retrieval-performance.json');args=a.parse_args();run=Path(args.run_dir).resolve()
 if (p.BASE/'outputs').resolve() not in run.parents:a.error('Invalid run directory')
 session=p.load_session(run);results=json.loads((run/'performance-results.json').read_text(encoding='utf-8'))['results']
 target={r['id'] for group in json.loads((run/'corpus-snapshot.json').read_text(encoding='utf-8')).values() for r in group}
 selected=[]
 for group in ['small','large']:
  rows=[(k,v) for k,v in results.items() if k in target and v['state']=='READY' and v.get('group')==group]
  rows.sort(key=lambda kv:kv[1]['bytes'])
  if rows:selected.extend([rows[i] for i in sorted(set([0,len(rows)//2,len(rows)-1]))])
 measures=[]
 def request(item):
  key,r=item;start=time.monotonic();question='报告中2024年的收入与研发投入分别是多少？' if r['language']=='zh' else 'What were revenue and research and development expenditure in 2024?'
  try:
   answer=p.api('/documents/query/preview',{'documentIds':[session['documents'][key]['document_id']],'question':question,'limit':200},session['token'])
   assert answer.get('llmCalls')==0
   p.save(run/'retrieval-previews'/f'{Path(args.output_file).stem}-{concurrency}-{key}.json',answer)
   return {'id':key,'group':r['group'],'language':r['language'],'seconds':round(time.monotonic()-start,3),'backend_ms':answer.get('latencyMs'),'ready':answer.get('ready'),'sources':answer.get('selectedSourceCount'),'strategy':answer.get('queryStrategy'),'blockedReasons':[t.get('blockedReason') for t in answer.get('retrieval',[]) if t.get('blockedReason')],'error':None}
  except Exception as e:return {'id':key,'seconds':round(time.monotonic()-start,3),'error':str(e)}
 for concurrency in args.concurrencies:
  start=time.monotonic()
  with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
   rows=list(pool.map(request,selected))
  measures.append({'concurrency':concurrency,'batch_seconds':round(time.monotonic()-start,3),'requests':rows})
  p.save(run/args.output_file,{'note':'Size-stratified documents; not quality scoring; warm/model-cache effects uncontrolled. HTTP 200 with ready=false is not a usable retrieval.','measurements':measures})
  print(json.dumps({'event':'retrieval_benchmark','concurrency':concurrency,'seconds':measures[-1]['batch_seconds'],'errors':sum(bool(r['error']) for r in rows)}),flush=True)
 audit=p.audit(session);p.save(run/'no-llm-audit.json',audit);assert audit['llmCalls']==0
 (run/'retrieval-done.marker').write_text('complete\n',encoding='utf-8')
if __name__=='__main__':main()
