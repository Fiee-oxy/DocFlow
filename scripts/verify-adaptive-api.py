"""Live authenticated upload/Docling/strategy/SSE checks. Never calls the LLM.
Creates isolated temporary accounts and documents, then removes only those fixtures.
"""
import io,json,uuid,time,zipfile,urllib.request,urllib.parse,urllib.error,subprocess,re

BASE="http://127.0.0.1:9090"
accounts=[];document_ids=[];task_ids=[];checks=[]
def call(path,body=None,token=None,method=None,form=False,raw=None,content_type=None):
    data=raw if raw is not None else (urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()) if body is not None else None
    headers={}
    if token:headers["Authorization"]="Bearer "+token
    if data is not None:headers["Content-Type"]=content_type or ("application/x-www-form-urlencoded" if form else "application/json")
    request=urllib.request.Request(BASE+path,data=data,headers=headers,method=method)
    with urllib.request.urlopen(request,timeout=20) as response:
        return json.loads(response.read())["data"]
def account():
    name="codex_rag_"+uuid.uuid4().hex[:12];password=uuid.uuid4().hex+"A1!"
    call("/user/register",{"username":name,"password":password,"nickname":"Temporary RAG verification"})
    data=call("/user/login",{"username":name,"password":password})
    accounts.append((name,int(data["userInfo"]["id"]),data["token"]));return data["token"]
def sql(text):
    result=subprocess.run(["docker","exec","-i","dovideo-ai-main-mysql-1","sh","-c",
        'export MYSQL_PWD=$MYSQL_ROOT_PASSWORD; exec mysql -uroot --default-character-set=utf8mb4 media_db'],input=text,text=True,capture_output=True)
    if result.returncode:raise RuntimeError("Fixture SQL failed")
    return result.stdout
try:
    owner=account();other=account()
    file=io.BytesIO()
    with zipfile.ZipFile(file,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",'''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>''')
        z.writestr("_rels/.rels",'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>''')
        z.writestr("word/document.xml",'''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Redis 令牌桶用于接口限流，RocketMQ 用于异步处理。</w:t></w:r></w:p></w:body></w:document>''')
    content=file.getvalue();doc=call("/documents/init-upload",{"filename":"adaptive-fixture.docx","size":len(content)},owner,form=True);document_ids.append(doc)
    request_id=str(uuid.uuid4());task_ids.append(request_id)
    request={"documentIds":[doc],"question":"总结全文","requestId":request_id}
    call("/documents/query",request,owner)
    for _ in range(40):
        task=call("/documents/query-tasks/"+request_id,token=owner)
        if task["state"] in ["FAILED","CANCELLED","COMPLETED"]:break
        time.sleep(.25)
    assert task["state"]=="FAILED" and "尚未解析完成" in task["error_message"];checks.append("incomplete_global_refused_before_llm")
    assert task["llm_calls"]==0
    replay=call("/documents/query",request,owner);assert replay["id"]==request_id;checks.append("request_id_reuse")
    for path in ["/documents/"+doc+"/strategy","/documents/query-tasks/"+request_id]:
        try:call(path,token=other);raise AssertionError("Unauthorized read succeeded")
        except urllib.error.HTTPError as e:assert e.code==403
    checks.append("live_owner_authorization")
    event_request=urllib.request.Request(BASE+"/documents/query-tasks/"+request_id+"/events",headers={"Authorization":"Bearer "+owner})
    with urllib.request.urlopen(event_request,timeout=20) as response:events=response.read().decode()
    assert "event:error" in events or "event: error" in events;checks.append("live_sse_event_replay")
    boundary="----codex"+uuid.uuid4().hex
    multipart=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="adaptive-fixture.docx"\r\nContent-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n').encode()+content+f"\r\n--{boundary}--\r\n".encode()
    call("/documents/upload-chunk?"+urllib.parse.urlencode({"uploadId":doc,"chunkIndex":0}),token=owner,method="POST",raw=multipart,content_type="multipart/form-data; boundary="+boundary)
    call("/documents/complete-upload",{"uploadId":doc},owner,form=True)
    for _ in range(120):
        status=call("/documents/"+doc,token=owner)
        if status["status"] in ["COMPLETED","FAILED"]:break
        time.sleep(1)
    assert status["status"]=="COMPLETED",status
    strategy=call("/documents/"+doc+"/strategy",token=owner)
    assert strategy["strategy"]=="DIRECT" and strategy["state"]=="DIRECT_READY" and not strategy["requested"];checks.append("live_upload_docling_direct_ready")
    chunks=call("/documents/"+doc+"/chunks",token=owner);assert "Redis" in chunks["chunks"][0]["text"];checks.append("saved_source_preview")
    count=sql("SELECT COUNT(*) FROM document_rag_chunks WHERE document_id='"+doc+"';")
    assert count.strip().splitlines()[-1]=="0";checks.append("short_document_no_rag_chunks")
    structured=sql("SELECT JSON_CONTAINS_PATH(payload,'one','$.doclingDocument') FROM document_parse_batches WHERE document_id='"+doc+"' LIMIT 1;")
    assert structured.strip().splitlines()[-1]=="1";checks.append("full_docling_batch_preserved")
    preview=call("/documents/query/preview",{"documentIds":[doc],"question":"Redis 的作用是什么？","limit":1},owner)
    assert preview["llmCalls"]==0 and preview["queryStrategy"]=="DIRECT_CONTEXT" and preview["ready"]
    assert "Redis" in preview["evidence"][0]["text"] and preview["evidence"][0]["filename"]=="adaptive-fixture.docx"
    checks.append("live_preview_short_sources_no_llm")
    plan=call("/documents/query/preview",{"documentIds":[doc],"question":"总结全文"},owner)
    assert plan["mode"]=="GLOBAL_ANALYSIS_PLAN" and plan["mapGroupCount"]>=1 and plan["llmCalls"]==0
    checks.append("live_preview_global_plan_no_llm")
    try:call("/documents/query/preview",{"documentIds":[doc],"question":"Redis"},other);raise AssertionError("Unauthorized preview succeeded")
    except urllib.error.HTTPError as e:assert e.code==403
    checks.append("live_preview_owner_isolation")
    assert sql(f"SELECT COUNT(*) FROM document_chat_turns WHERE document_id='{doc}';").strip().splitlines()[-1]=="0"
    assert sql(f"SELECT COUNT(*) FROM document_query_tasks WHERE owner_id={accounts[0][1]};").strip().splitlines()[-1]=="1"
    checks.append("live_preview_no_chat_or_task_writes")
    print(json.dumps({"passed":checks,"count":len(checks),"llm_calls":0},ensure_ascii=False))
finally:
    for name,owner_id,token in accounts:
        try:call("/user/logout",{},token)
        except Exception:pass
    # Only generated fixture IDs and numeric fixture owners are used in cleanup.
    for task in task_ids:
        assert re.fullmatch(r"[0-9a-f-]{36}",task)
        sql(f"DELETE FROM document_query_events WHERE task_id='{task}'; DELETE FROM document_query_tasks WHERE id='{task}';")
    for doc in document_ids:
        assert re.fullmatch(r"[0-9a-f-]{36}",doc)
        for table in ["document_sections","document_rag_chunks","document_parse_batches","document_adaptive_state","document_chat_turns","document_parts","document_blocks","document_files"]:
            key="id" if table=="document_files" else "document_id"
            sql(f"DELETE FROM {table} WHERE {key}='{doc}';")
    for name,owner_id,token in accounts:
        assert re.fullmatch(r"codex_rag_[0-9a-f]{12}",name)
        sql(f"DELETE FROM users WHERE id={owner_id} AND username='{name}';")
        # Source files are confined to the generated account's own prefix.
        subprocess.run(["docker","exec","dovideo-ai-main-minio-1","sh","-c",
            'mc alias set codexfixture http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc rm --recursive --force codexfixture/media/documents/'+str(owner_id)+'/ >/dev/null'],capture_output=True)
