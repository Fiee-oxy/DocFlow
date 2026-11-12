"""Real upload -> MQ -> parser -> SQL -> BGE/Milvus with isolated evaluation owner."""
import importlib.util,json,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('pilot',Path(__file__).with_name('run-rag-quality-pilot.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
out=p.ROOT/'evaluation/parser-benchmark/e2e';out.mkdir(parents=True,exist_ok=True)
for attempt in range(60):
    try:s=p.load_session(out);break
    except Exception:time.sleep(1)
else:raise RuntimeError('Backend login not ready')
sources=[('native-long',p.ROOT/'server/target/parser-benchmark/long.docx'),('fast-pdf',p.ROOT/'evaluation/parser-benchmark/synthetic/text.pdf')]
results=[]
for key,path in sources:
    started=time.monotonic();p.upload(out,s,{'id':key,'path':str(path)})
    doc=s['documents'][key]['document_id'];deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        status=p.api('/documents/'+doc,token=s['token'])
        if status['status']=='FAILED':raise RuntimeError(str(status))
        if status['parseComplete']:break
        time.sleep(1)
    else:raise RuntimeError('Parsing timeout')
    p.api('/documents/'+doc+'/index',{},s['token'])
    while time.monotonic()<deadline:
        state=p.api('/documents/'+doc+'/strategy',token=s['token'])
        if state['state']=='FAILED':raise RuntimeError(str(state))
        if state['state']=='READY':break
        time.sleep(1)
    else:raise RuntimeError('Index timeout')
    audit=p.api('/documents/'+doc+'/parser-audit',token=s['token'])
    assert audit and any(e.get('documentIR') for e in audit)
    import urllib.request
    req=urllib.request.Request(p.API+'/documents/'+doc+'/ir?format=json&batch=0',headers={'Authorization':'Bearer '+s['token']})
    with urllib.request.urlopen(req,timeout=30) as r:ir=json.load(r)
    assert ir['documentId']==doc and ir['schemaVersion']=='1.0'
    if key=='native-long':assert not any(b['pages'] for b in ir['blocks'])
    rows=p.sql("SELECT JSON_OBJECT('chunks',COUNT(*)) FROM document_rag_chunks WHERE document_id='"+doc+"' AND index_version='"+state['version']+"';") if 'version' in state else []
    results.append({'source':key,'documentId':doc,'parseComplete':True,'indexState':state['state'],'indexedChunks':state.get('indexedChunks'),'irSchemaVersion':ir['schemaVersion'],'elapsed_ms':(time.monotonic()-started)*1000,'synthetic':True})
    p.save(out/'results.json',results);print(json.dumps(results[-1]),flush=True)
p.save(out/'no-llm-audit.json',p.audit(s));assert p.audit(s)['llmCalls']==0 and p.audit(s)['queryTasks']==0
