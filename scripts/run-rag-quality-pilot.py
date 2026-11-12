"""Actual isolated upload/index/preview pilot. Never calls chat or answer-generation endpoints."""
import argparse, hashlib, json, secrets, subprocess, time, urllib.error, urllib.parse, urllib.request, uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'evaluation/rag-quality'
API='http://127.0.0.1:9090'
SELECTED=['financebench-3m-2018','qasper-1912.01214','cn-xiaomi-mi2-manual','cn-shenzhen-grant-2026']

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.part')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)

def api(path,body=None,token=None,form=False,raw=None,content_type=None):
    # No callable path to paid model generation in this runner.
    route=path.split('?')[0]
    if route=='/documents/query' or route.endswith('/chat') or '/query-tasks/' in route:
        raise ValueError('Answer-generation endpoint forbidden in no-LLM evaluation')
    data=raw if raw is not None else None if body is None else urllib.parse.urlencode(body).encode() if form else json.dumps(body,ensure_ascii=False).encode()
    headers={'Content-Type':content_type or ('application/x-www-form-urlencoded' if form else 'application/json')}
    if token:headers['Authorization']='Bearer '+token
    request=urllib.request.Request(API+path,data=data,headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=240) as response: result=json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError('HTTP '+str(error.code)+' '+error.read().decode('utf-8','replace')[:500]) from None
    if result.get('code')!=0: raise RuntimeError(result.get('message','API rejected request'))
    return result.get('data')

def sql(query):
    result=subprocess.run(['docker','exec','-i','dovideo-ai-main-mysql-1','sh','-c',
        'export MYSQL_PWD=$MYSQL_ROOT_PASSWORD; exec mysql -uroot --default-character-set=utf8mb4 --batch --raw --skip-column-names media_db'],
        input=query,text=True,encoding='utf-8',capture_output=True,check=True)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

def load_session(run):
    private=run/'.session.json'
    if private.exists():
        session=json.loads(private.read_text(encoding='utf-8'))
        login=api('/user/login',{'username':session['username'],'password':session['password']})
        if int(login['userInfo']['id'])!=session['owner']: raise RuntimeError('Evaluation owner changed')
        session['token']=login['token']
    else:
        name='rageval_'+uuid.uuid4().hex[:14];password=secrets.token_urlsafe(32)
        api('/user/register',{'username':name,'password':password,'nickname':'Isolated no-LLM RAG evaluation'})
        login=api('/user/login',{'username':name,'password':password})
        session={'username':name,'password':password,'owner':int(login['userInfo']['id']),'token':login['token'],'documents':{}}
    save(private,session)
    return session

def upload(run,session,item):
    path=BASE/item['path'];data=path.read_bytes()
    digest=hashlib.sha256(data).hexdigest()
    key=item['id'];token=session['token']
    if key not in session['documents']:
        filename='eval_'+key+path.suffix
        doc=api('/documents/init-upload',{'filename':filename,'size':len(data)},token,form=True)
        uuid.UUID(doc)
        session['documents'][key]={'document_id':doc,'sha256':digest,'filename':filename}
        save(run/'.session.json',session)
    entry=session['documents'][key]
    if entry['sha256']!=digest: raise RuntimeError('Evaluation source content changed')
    doc=entry['document_id'];status=api('/documents/'+doc,token=token)
    if status['status']=='UPLOADING':
        existing=set(api('/documents/upload-status?uploadId='+doc,token=token))
        for index,start in enumerate(range(0,len(data),5*1024*1024)):
            if index in existing:continue
            boundary='----rageval'+uuid.uuid4().hex
            body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{entry["filename"]}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+data[start:start+5*1024*1024]+f'\r\n--{boundary}--\r\n'.encode()
            api('/documents/upload-chunk?'+urllib.parse.urlencode({'uploadId':doc,'chunkIndex':index}),token=token,raw=body,content_type='multipart/form-data; boundary='+boundary)
        api('/documents/complete-upload',{'uploadId':doc},token,form=True)
    print(json.dumps({'event':'uploaded','source':key,'documentId':doc}),flush=True)

def ingest(run,session,items):
    for item in items:upload(run,session,item)
    deadline=time.monotonic()+1200;requested=set();last_print=0
    states={}
    while time.monotonic()<deadline:
        finished=True
        for item in items:
            key=item['id'];doc=session['documents'][key]['document_id'];status=api('/documents/'+doc,token=session['token'])
            state=api('/documents/'+doc+'/strategy',token=session['token'])
            if status['status']=='COMPLETED' and state['state'] not in ['READY','INDEXING','PARTIAL_READY','FAILED'] and key not in requested:
                api('/documents/'+doc+'/index',{},session['token']);requested.add(key)
                state=api('/documents/'+doc+'/strategy',token=session['token'])
            states[key]={'document_id':doc,'source_sha256':session['documents'][key]['sha256'],'language':item['language'],
                'source_path':item['path'],'status':status,'strategy':state}
            if status['status']!='FAILED' and state['state']!='FAILED' and not(status['status']=='COMPLETED' and state['state']=='READY'):finished=False
        save(run/'ingestion.json',states)
        if time.monotonic()-last_print>20 or finished:
            print(json.dumps({'event':'ingestion_progress','documents':{key:{'parse':v['status']['status'],'pages':v['status'].get('parsedPages'),'index':v['strategy']['state'],'chunks':v['strategy']['indexedChunks']} for key,v in states.items()}}),flush=True);last_print=time.monotonic()
        if finished:break
        time.sleep(2)
    for key,value in states.items():
        doc=value['document_id'];uuid.UUID(doc);owner=int(session['owner'])
        # Authorization guard is repeated in the read-only database export.
        chunks=sql(f"SELECT JSON_OBJECT('chunk_id',r.chunk_id,'content',r.content,'search_text',r.search_text,'section_id',r.section_id,'blockIndex',r.block_index,'sources',JSON_EXTRACT(r.source_json,'$')) FROM document_rag_chunks r JOIN document_files f ON f.id=r.document_id WHERE f.user_id={owner} AND r.document_id='{doc}' AND r.index_version=(SELECT index_version FROM document_adaptive_state WHERE document_id='{doc}') ORDER BY r.block_index;")
        source=[];page=1
        while True:
            rows=api('/documents/'+doc+'/chunks?page='+str(page)+'&size=50',token=session['token'])
            source.extend(rows['chunks'])
            if len(source)>=rows['totalBlocks'] or not rows['chunks']:break
            page+=1
        save(run/'documents'/key/'parsed.json',source);save(run/'documents'/key/'rag_chunks.json',chunks)
    save(run/'no-llm-audit.json',audit(session))
    print(json.dumps({'event':'ingestion_finished','runDirectory':str(run),'noLlmAudit':audit(session)}),flush=True)

def audit(session):
    owner=int(session['owner'])
    return sql(f"SELECT JSON_OBJECT('owner', {owner}, 'queryTasks',COUNT(*),'llmCalls',COALESCE(SUM(llm_calls),0)) FROM document_query_tasks WHERE owner_id={owner};")[0]

def previews(run,session):
    cases=json.loads((BASE/'pilot-cases.json').read_text(encoding='utf-8'))
    for case in cases:
        key=case['document_key']
        if key not in session['documents']:continue
        started=time.monotonic();result_path=run/'previews'/(case['case_id']+'.json')
        if result_path.exists():continue
        try:
            data=api('/documents/query/preview',{'documentIds':[session['documents'][key]['document_id']],
                'question':case['question'],'limit':200},session['token'])
            if data.get('llmCalls')!=0: raise RuntimeError('Unexpected model call reported')
            save(result_path,data)
            print(json.dumps({'event':'preview_finished','case':case['case_id'],'language':case['language'],'ready':data.get('ready'),'sources':data.get('selectedSourceCount'),'seconds':round(time.monotonic()-started,2)}),flush=True)
        except Exception as error:
            save(result_path,{'evaluation_error':str(error),'question':case['question'],'llmCalls':0})
            print(json.dumps({'event':'preview_failed','case':case['case_id'],'error':str(error)}),flush=True)
    verification=audit(session);save(run/'no-llm-audit.json',verification)
    assert verification['llmCalls']==0 and verification['queryTasks']==0

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['ingest','preview'])
    parser.add_argument('--run-dir',required=True)
    args=parser.parse_args();run=Path(args.run_dir).resolve()
    if (BASE/'outputs').resolve() not in run.parents:parser.error('Run directory must be inside evaluation outputs')
    run.mkdir(parents=True,exist_ok=True)
    session=load_session(run)
    diagnostic=api('/documents/query/diagnostics',token=session['token']);save(run/'runtime.json',diagnostic)
    catalog=json.loads((BASE/'sources.json').read_text(encoding='utf-8'))
    items=[item for item in catalog['files'] if item['id'] in SELECTED]
    if args.phase=='ingest':ingest(run,session,items)
    else:previews(run,session)

if __name__=='__main__':main()
