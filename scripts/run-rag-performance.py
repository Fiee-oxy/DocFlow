"""Bounded end-to-end ingestion benchmark; chat LLM endpoints forbidden."""
import importlib.util,json,time,argparse
from pathlib import Path
spec=importlib.util.spec_from_file_location('pilot',Path(__file__).with_name('run-rag-quality-pilot.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
def main():
 a=argparse.ArgumentParser();a.add_argument('--concurrency',type=int,default=2);a.add_argument('--run-dir',required=True);a.add_argument('--follow-corpus',action='store_true');args=a.parse_args()
 if not 1<=args.concurrency<=4:a.error('Concurrency must be 1..4')
 run=Path(args.run_dir).resolve()
 if (p.BASE/'outputs').resolve() not in run.parents:a.error('Run must be inside evaluation outputs')
 run.mkdir(parents=True,exist_ok=True);session=p.load_session(run)
 corpus=json.loads((p.BASE/'performance-corpus.json').read_text(encoding='utf-8'))
 items=corpus['selected']['small']+corpus['selected']['large']
 p.save(run/'corpus-snapshot.json',corpus['selected']);p.save(run/'runtime.json',p.api('/documents/query/diagnostics',token=session['token']))
 previous=run/'performance-results.json'
 results=json.loads(previous.read_text(encoding='utf-8'))['results'] if previous.exists() else {}
 active={};queue=[i for i in items if i['id'] not in results or results[i['id']]['state']=='RUNNING'];started=time.time();last=0
 p.save(run/'benchmark-config.json',{'concurrency':args.concurrency,'documents':len(items),'clock':'wall time seconds; includes queue within pipeline','chat_llm':False})
 admitted=set(results)|{i['id'] for i in items}
 corpus_complete=len(items)>=30
 while queue or active or (args.follow_corpus and not corpus_complete and time.time()-started<7200):
  if args.follow_corpus:
   try:
    latest=json.loads((p.BASE/'performance-corpus.json').read_text(encoding='utf-8'))
    corpus_complete=sum(len(v) for v in latest['selected'].values())>=30
    for item in latest['selected']['small']+latest['selected']['large']:
     if item['id'] not in admitted:queue.append(item);admitted.add(item['id'])
    p.save(run/'corpus-snapshot.json',latest['selected'])
   except (json.JSONDecodeError,OSError):pass
  while queue and len(active)<args.concurrency:
   item=queue.pop(0);key=item['id'];t=time.time()
   try:
    p.upload(run,session,item)
    active[key]={'item':item,'started':t,'upload_seconds':round(time.time()-t,3),'index_requested':False}
    results[key]={'group':item['group'],'language':item['language'],'bytes':item['bytes'],'pages':item['pages'],'text_characters':item['text_characters'],'started_at':t,'upload_seconds':active[key]['upload_seconds'],'state':'RUNNING'}
   except Exception as e:results[key]={'state':'UPLOAD_FAILED','error':str(e)}
  for key,state in list(active.items()):
   elapsed=round(time.time()-state['started'],3);doc=session['documents'][key]['document_id'];r=results[key]
   try:
    status=p.api('/documents/'+doc,token=session['token']);index=p.api('/documents/'+doc+'/strategy',token=session['token'])
    r.update(parse_status=status['status'],index_status=index['state'],parsed_pages=status.get('parsedPages'),indexed_chunks=index.get('indexedChunks'))
    if index.get('indexedChunks',0)>0 and 'first_indexed_seconds' not in r:r['first_indexed_seconds']=elapsed
    if status['status']=='COMPLETED':
     r.setdefault('parse_complete_seconds',elapsed)
     if index['state'] not in ['READY','INDEXING','PARTIAL_READY','FAILED'] and not state['index_requested']:
      p.api('/documents/'+doc+'/index',{},session['token']);state['index_requested']=True
    if status['status']=='FAILED' or index['state']=='FAILED':
     r.update(state='FAILED',elapsed_seconds=elapsed,error=status.get('errorMessage') or index.get('error'));del active[key]
    elif status['status']=='COMPLETED' and index['state']=='READY':
     r.update(state='READY',index_complete_seconds=elapsed);del active[key]
    elif elapsed>3600:r.update(state='TIMEOUT',elapsed_seconds=elapsed);del active[key]
   except Exception as e:r['poll_error']=str(e)
  p.save(run/'performance-results.json',{'elapsed_seconds':round(time.time()-started,2),'results':results,'pending':len(queue),'active':list(active)})
  if time.time()-last>20:
   print(json.dumps({'event':'performance_progress','ready':sum(r['state']=='READY' for r in results.values()),'failed':sum(r['state'] in ['FAILED','UPLOAD_FAILED','TIMEOUT'] for r in results.values()),'pending':len(queue),'active':{k:{x:results[k].get(x) for x in ['parsed_pages','indexed_chunks','parse_status','index_status']} for k in active}}),flush=True);last=time.time()
  if active or (args.follow_corpus and not corpus_complete):time.sleep(2)
 audit=p.audit(session);p.save(run/'no-llm-audit.json',audit)
 assert audit['llmCalls']==0 and audit['queryTasks']==0
 print(json.dumps({'event':'performance_finished','documents':len(results),'audit':audit}),flush=True)
if __name__=='__main__':main()
