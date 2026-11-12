"""Compare identical chunk text under two isolated document IDs, no LLM."""
import importlib.util,json,hashlib,uuid
from pathlib import Path
s=importlib.util.spec_from_file_location('p',Path(__file__).with_name('run-rag-quality-pilot.py'));p=importlib.util.module_from_spec(s);s.loader.exec_module(p)
root=p.BASE/'outputs';run=root/'recall-20261014';question='怎样让某个后台程序不被一键清除结束？'
equivalence={}
for name,folder,key in [('pilot_document','pilot-20261013','cn-xiaomi-mi2-manual'),('performance_document','performance-20261013','perf-a73aca5941761ea9')]:
 session=p.load_session(root/folder);doc=session['documents'][key]['document_id'];result=p.api('/documents/query/preview',{'documentIds':[doc],'question':question,'limit':200},session['token']);assert result['llmCalls']==0
 uuid.UUID(doc);owner=int(session['owner'])
 rows=p.sql(f"SELECT JSON_OBJECT('content',r.content,'search_text',r.search_text) FROM document_rag_chunks r JOIN document_files f ON f.id=r.document_id WHERE f.user_id={owner} AND r.document_id='{doc}' AND r.index_version=(SELECT index_version FROM document_adaptive_state WHERE document_id='{doc}') ORDER BY r.block_index;")
 equivalence[name]={'rows':len(rows),'content_hash':hashlib.sha256(json.dumps([r['content'] for r in rows],ensure_ascii=False).encode()).hexdigest(),'search_text_hash':hashlib.sha256(json.dumps([r['search_text'] for r in rows],ensure_ascii=False).encode()).hexdigest(),'first_search_prefix':rows[0]['search_text'][:100]}
 p.save(run/'rechecks'/(name+'.json'),result);trace=result['retrieval'][0]
 print(json.dumps({'document':name,'ready':result['ready'],'selected':result.get('selectedSourceCount'),'latencyMs':result['latencyMs'],'dense_first_pages':[[s.get('pages') for s in h.get('sources',[])] for h in trace.get('dense',[])[:3]]}),flush=True)
p.save(run/'rechecks/source-equivalence.json',equivalence)
