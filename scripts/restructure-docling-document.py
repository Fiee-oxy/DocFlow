"""Replay saved Docling heading metadata, then start a new index version. No re-parsing/LLM.
Run while the backend is stopped. Scoped to an explicitly supplied document UUID.
"""
import sys,json,subprocess,uuid,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/docling'))
from normalization import annotate_batch
doc=str(uuid.UUID(sys.argv[1]))
def sql(query):
    r=subprocess.run(['docker','exec','-i','dovideo-ai-main-mysql-1','sh','-c','export MYSQL_PWD=$MYSQL_ROOT_PASSWORD; exec mysql -uroot --default-character-set=utf8mb4 --batch --raw --skip-column-names media_db'],input=query,text=True,encoding='utf-8',capture_output=True,check=True)
    return r.stdout.strip()
def value(text):return "CONVERT(0x"+str(text).encode().hex()+" USING utf8mb4)" if text else "''"
rows=sql(f"SELECT batch_index,payload FROM document_parse_batches WHERE document_id='{doc}' ORDER BY batch_index;")
commands=['START TRANSACTION;'];heading=[];refs={};section_rows={}
for row in rows.splitlines():
    batch_index,payload=row.split('\t',1);batch=json.loads(payload)
    if not batch.get('doclingDocument'):raise RuntimeError('Only complete Docling batches can be replayed')
    heading=annotate_batch(batch,heading)
    commands.append(f"UPDATE document_parse_batches SET payload={value(json.dumps(batch,ensure_ascii=False))} WHERE document_id='{doc}' AND batch_index={int(batch_index)};")
    for b in batch['blocks']:refs[f'b{batch_index}:'+b['ref']]=b['section']
blocks=sql(f"SELECT block_index,source_json FROM document_blocks WHERE document_id='{doc}' ORDER BY block_index;")
commands.append(f"DELETE FROM document_sections WHERE document_id='{doc}';")
for row in blocks.splitlines():
    index,payload=row.split('\t',1);sources=json.loads(payload)
    for s in sources:
        title=refs.get(s['ref'],s.get('title_path','正文'));s['title_path']=title
        parent=None;path=''
        for part in title.split(' / '):
            path=part if not path else path+' / '+part;sid=hashlib.sha256(path.encode()).hexdigest()
            entry=section_rows.setdefault(sid,dict(parent=parent,title=part,path=path,index=int(index),pages=[]));entry['pages']+=s.get('pages',[]);parent=sid
    commands.append(f"UPDATE document_blocks SET section_title={value(title[:512])},source_json={value(json.dumps(sources,ensure_ascii=False))} WHERE document_id='{doc}' AND block_index={int(index)};")
for sid,e in section_rows.items():
    parent=value(e['parent']) if e['parent'] else 'NULL';lo=str(min(e['pages'])) if e['pages'] else 'NULL';hi=str(max(e['pages'])) if e['pages'] else 'NULL'
    commands.append(f"INSERT INTO document_sections(document_id,section_id,parent_id,title,title_path,section_order,start_page,end_page) VALUES('{doc}','{sid}',{parent},{value(e['title'])},{value(e['path'])},{e['index']},{lo},{hi});")
version='structure-v3-'+uuid.uuid4().hex[:8]
commands.append(f"UPDATE document_adaptive_state SET index_version='{version}',indexed_blocks=0,indexed_batches=0,index_requested=TRUE,state='INDEXING',index_attempts=0,error_message=NULL,next_retry_at=NULL WHERE document_id='{doc}';")
commands.append('COMMIT;');sql('\n'.join(commands))
print(json.dumps(dict(document=doc,version=version,sections=len(section_rows),batches=len(rows.splitlines())),ensure_ascii=False))
