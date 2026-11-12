"""Authenticated HTTP verification. 20 unique pipelines, real models, no caches seeded.

Upload/index readiness and query submission have separate barriers, explicitly
reported. Thread count is offered client concurrency, not model concurrency.
"""
import concurrent.futures,hashlib,io,json,statistics,threading,time,urllib.request,urllib.parse,urllib.error,uuid,zipfile
from pathlib import Path
import fitz
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evaluation/final-release-20261112';BASE='http://127.0.0.1:9090'

def save(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def api(path,body=None,token=None,method=None,raw=None,content_type=None):
    headers={};data=raw if raw is not None else json.dumps(body).encode() if body is not None else None
    if token:headers['Authorization']='Bearer '+token
    if data is not None:headers['Content-Type']=content_type or 'application/json'
    req=urllib.request.Request(BASE+path,data=data,headers=headers,method=method)
    with urllib.request.urlopen(req,timeout=45) as response:
        result=json.load(response)
        if result.get('code')!=0:raise RuntimeError('Business error '+str(result.get('code'))+': '+str(result.get('message')))
        return result.get('data')

def upload_part(doc,index,data,token,declared=None):
    boundary='----final'+uuid.uuid4().hex
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="part.bin"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+data+f'\r\n--{boundary}--\r\n'.encode()
    return api('/documents/upload-chunk?'+urllib.parse.urlencode(dict(uploadId=doc,chunkIndex=index,sha256=declared or hashlib.sha256(data).hexdigest())),token=token,method='POST',raw=body,content_type='multipart/form-data; boundary='+boundary)

def complete(doc,token):return api('/documents/complete-upload?uploadId='+doc,token=token,method='POST')

def account(i):
    name='final_'+uuid.uuid4().hex[:12];password=uuid.uuid4().hex+'A1!'
    api('/user/register',dict(username=name,password=password,nickname='Final verification '+str(i)))
    result=api('/user/login',dict(username=name,password=password))
    return dict(username=name,password=password,token=result['token'],ownerId=result['userInfo']['id'])

def pdf_fixture(number):
    pdf=fitz.open();code='RUN-'+str(number).zfill(3)+'-'+uuid.uuid4().hex[:6].upper()
    for page_number in range(12):
        page=pdf.new_page();title='Operations guide '+str(number)+' / section '+str(page_number+1)
        page.insert_text((40,35),title,fontsize=14)
        lines=[]
        if page_number in (0,6):lines.append('Recovery verification code: '+code+'. Rate limiting algorithm: token bucket.')
        for n in range(37):
            lines.append(f'Procedure {page_number+1}.{n+1}: save committed progress, validate input and record source information.')
        page.insert_textbox(fitz.Rect(40,55,555,790),'\n'.join(lines),fontsize=9)
    return pdf.tobytes(),code

def docx_fixture(number):
    code='RUN-'+str(number).zfill(3)+'-'+uuid.uuid4().hex[:6].upper();stream=io.BytesIO()
    text=['Recovery verification code: '+code+'. Rate limiting algorithm: token bucket.']
    text += [f'Procedure {i}: preserve complete paragraphs and tables, record committed progress and retry uncommitted work. ' * 3 for i in range(130)]
    text.append(text[0])
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+''.join('<w:p><w:r><w:t>'+s+'</w:t></w:r></w:p>' for s in text)+'</w:body></w:document>')
    return stream.getvalue(),code

def await_ready(doc,token,timeout=900):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        record=api('/documents/'+doc,token=token)
        if record['status'] in ('FAILED','CANCELLED'):raise RuntimeError('Parse failed: '+str(record))
        if record['parseComplete']:
            state=api('/documents/'+doc+'/strategy',token=token)
            if state['state']=='FAILED':raise RuntimeError('Index failed: '+str(state))
            if state['state']=='READY':return record,state
        time.sleep(1)
    raise TimeoutError('Document readiness deadline')

def events(task,token,after=0,stop_at_delta=False):
    req=urllib.request.Request(BASE+'/documents/query-tasks/'+task+'/events?after='+str(after),headers={'Authorization':'Bearer '+token,'Accept':'text/event-stream','Last-Event-ID':str(after)})
    seen=[];last=after;frame=[]
    with urllib.request.urlopen(req,timeout=90) as response:
        while True:
            line=response.readline().decode('utf-8')
            if not line:break
            if line.strip():frame.append(line.rstrip('\r\n'));continue
            data='\n'.join(s[5:].lstrip() for s in frame if s.startswith('data:'))
            kind=next((s[6:].strip() for s in frame if s.startswith('event:')),'message')
            event_id=next((int(s[3:].strip()) for s in frame if s.startswith('id:')),None);frame=[]
            if not data:continue
            if event_id is not None:
                assert event_id>last;last=event_id
            payload=json.loads(data);seen.append(dict(id=event_id,type=kind,data=payload,at=time.monotonic()))
            if stop_at_delta and kind=='delta':return seen,last
            if kind in ('complete','error','task_status'):return seen,last
    return seen,last

def prepare(i,actor,barrier):
    token=actor['token'];data,code=docx_fixture(i) if i%4==0 else pdf_fixture(i);extension='docx' if i%4==0 else 'pdf'
    barrier.wait();start=time.monotonic()
    body=dict(filename=f'final-{i}.{extension}',sizeBytes=len(data),sha256=hashlib.sha256(data).hexdigest())
    session=api('/documents/uploads/init',body,token);doc=session['uploadId']
    chunksize=session['chunkSizeBytes']
    for n,offset in enumerate(range(0,len(data),chunksize)):upload_part(doc,n,data[offset:offset+chunksize],token)
    accepted=complete(doc,token);accepted_at=time.monotonic()
    record,state=await_ready(doc,token)
    return dict(i=i,actor=i%4,doc=doc,code=code,kind=extension,bytes=len(data),uploadBody=body,start=start,
        acceptedMs=(accepted_at-start)*1000,readyMs=(time.monotonic()-start)*1000,state=state,parseBatches=record.get('parseBatches'))

def query(case,actor,barrier,question='What is the recovery verification code and the rate limiting algorithm in this document?'):
    token=actor['token'];task=str(uuid.uuid4())
    barrier.wait();start=time.monotonic()
    body=dict(documentIds=[case['doc']],question=question,requestId=task)
    accepted=api('/documents/query',body,token);accepted_ms=(time.monotonic()-start)*1000
    first,cursor=events(task,token,stop_at_delta=True)
    first_delta=next((x for x in first if x['type']=='delta'),None)
    rest,cursor=events(task,token,cursor) if first_delta else ([],cursor)
    task_result=api('/documents/query-tasks/'+task,token=token)
    if task_result['state']!='COMPLETED':raise RuntimeError('Query did not complete: '+str(task_result))
    result=json.loads(task_result['result_json']);assert case['code'] in result['answer'],result
    assert result.get('citations'),result
    calls=task_result['llm_calls'];api('/documents/query',body,token)
    assert api('/documents/query-tasks/'+task,token=token)['llm_calls']==calls
    return dict(i=case['i'],task=task,acceptedMs=accepted_ms,firstDeltaMs=None if first_delta is None else (first_delta['at']-start)*1000,
        queryMs=(time.monotonic()-start)*1000,endToEndMs=(time.monotonic()-case['start'])*1000,
        llmCalls=calls,reconnected=bool(first_delta),eventTypes=[x['type'] for x in first+rest],
        state=task_result['state'],answer=result,usageUnknown=task_result['usage_unknown'],idempotent=True)

def main():
    OUT.mkdir(parents=True,exist_ok=True);actors=[account(i) for i in range(4)]
    save('session.private.json',dict(actors=actors))
    save('protocol.json',dict(concurrency=20,accounts=4,uniqueFiles=20,pdfs=15,docx=5,
        phases='20 uploads concurrently; wait all indexed; 20 query submissions concurrently',
        modelConcurrency='backend bounded workers; NOT 20 simultaneous model inferences',
        outputTokens=512,syntheticFunctionalFixtures=True,performanceClaim='Not a large-file capacity or stable P95 benchmark'))
    prepared=[];results=[];failures=[]
    barrier=threading.Barrier(20)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        futures={pool.submit(prepare,i,actors[i%4],barrier):i for i in range(20)}
        for future in concurrent.futures.as_completed(futures):
            i=futures[future]
            try:prepared.append(future.result());print('indexed '+str(i),flush=True)
            except Exception as e:failures.append(dict(i=i,phase='INGEST',error=type(e).__name__+': '+str(e)));print('ingest failed '+str(i),flush=True)
            save('ingestion.json',dict(prepared=prepared,failures=failures))
    if len(prepared)!=20:
        save('results.json',dict(completed=False,failures=failures));return
    barrier=threading.Barrier(20)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        futures={pool.submit(query,c,actors[c['actor']],barrier):c['i'] for c in prepared}
        for future in concurrent.futures.as_completed(futures):
            i=futures[future]
            try:results.append(future.result());print('answered '+str(i),flush=True)
            except Exception as e:failures.append(dict(i=i,phase='QUERY',error=type(e).__name__+': '+str(e)));print('query failed '+str(i),flush=True)
            save('results.json',dict(completed=len(results)==20,results=results,failures=failures))
    print(json.dumps(dict(ingested=len(prepared),answered=len(results),failures=len(failures))),flush=True)
if __name__=='__main__':main()
