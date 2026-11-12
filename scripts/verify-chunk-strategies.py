"""Live authenticated preview and default asynchronous index snapshot smoke; no chat calls."""
import importlib.util,json,time,uuid
from pathlib import Path
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('pilot',root/'scripts/run-rag-quality-pilot.py');p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
run=root/'evaluation/parser-rollback-20261018';out=root/'evaluation/chunk-strategies-20261019';out.mkdir(exist_ok=True)
session=p.load_session(run);rows=[]
for key in ['rollback-pdf','rollback-docx']:
 did=session['documents'][key]['document_id'];uuid.UUID(did)
 for strategy in ['legacy','boundary']:
  body={'batchIndex':0,'strategy':strategy,'targetTokens':350,'maxTokens':480,'minPreferredTokens':80,'mergePeers':True}
  before=p.api('/documents/'+did+'/strategy',token=session['token'])
  result=p.api('/documents/'+did+'/chunk-preview',body,session['token'])
  after=p.api('/documents/'+did+'/strategy',token=session['token'])
  assert result['writes']==0 and result['llmCalls']==0 and result['chunks']
  assert before['version']==after['version'] and before['indexedChunks']==after['indexedChunks']
  rows.append({'format':key,'strategy':strategy,'count':result['count'],'noIndexMutation':True})
  p.save(out/(key+'-'+strategy+'-preview.json'),result)
# Rebuild only this isolated evaluation account's fixture; real user documents untouched.
did=session['documents']['rollback-pdf']['document_id']
p.api('/documents/'+did+'/index/rebuild',{},session['token'])
for _ in range(90):
 state=p.api('/documents/'+did+'/strategy',token=session['token'])
 if state['state']=='READY':break
 time.sleep(1)
else:raise RuntimeError('Isolated asynchronous index did not become ready')
snap=p.sql("SELECT JSON_OBJECT('configuration',configuration_json) FROM document_chunk_configuration WHERE document_id='"+did+"' AND index_version='"+state['version']+"';")
assert snap and json.loads(snap[0]['configuration'])['strategy']=='legacy'
p.save(out/'smoke.json',{'previews':rows,'asyncDefaultIndexReady':True,'configurationSnapshot':snap,'llmCalls':0})
print(json.dumps({'previews':len(rows),'asyncDefaultIndexReady':True,'llmCalls':0}))
