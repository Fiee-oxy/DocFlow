"""Check published chunk provenance against canonical section paths; no model calls."""
import sys,json,re,uuid,subprocess
doc=str(uuid.UUID(sys.argv[1]))
query=f"SELECT batch_index,payload FROM document_parse_batches WHERE document_id='{doc}' ORDER BY batch_index; SELECT c.chunk_id,c.source_json FROM document_rag_chunks c JOIN document_adaptive_state s ON s.document_id=c.document_id AND s.index_version=c.index_version WHERE c.document_id='{doc}';"
r=subprocess.run(['docker','exec','-i','dovideo-ai-main-mysql-1','sh','-c','export MYSQL_PWD=$MYSQL_ROOT_PASSWORD; exec mysql -uroot --default-character-set=utf8mb4 --batch --raw --skip-column-names media_db'],input=query,text=True,encoding='utf-8',capture_output=True,check=True)
refs={};chunks=[];known_refs=set()
for line in r.stdout.strip().splitlines():
    key,payload=line.split('\t',1);value=json.loads(payload)
    if isinstance(value,dict):
        for b in value['blocks']:refs[f'b{key}:'+b['ref']]=b['section']
        for collection in ('texts','tables','pictures','groups'):
            for item in value.get('doclingDocument',{}).get(collection,[]):
                known_refs.add(f'b{key}:'+item['self_ref'])
    else:chunks.append((key,value))
violations=[];unknown=0;unknown_examples=[];raw_only=0
for key,sources in chunks:
    for source in sources:
        ref=source.get('ref','');match=re.match(r'^(b\d+:)(.*)',ref)
        if not match:unknown+=1;continue
        paths={refs[match[1]+part.strip()] for part in match[2].split(',') if match[1]+part.strip() in refs}
        if not paths:
            if all(match[1]+part.strip() in known_refs for part in match[2].split(',')):raw_only+=1
            else:unknown+=1;unknown_examples.append(dict(ref=ref,section=source.get('section')))
        if len(paths)>1 or paths and source.get('section') not in paths:violations.append(dict(chunk=key,paths=sorted(paths),assigned=source.get('section')))
report=dict(chunks=len(chunks),violations=violations,unknownSourceGroups=unknown,rawDoclingOnlySourceGroups=raw_only,unknownExamples=unknown_examples[:5],llmCalls=0)
print(json.dumps(report,ensure_ascii=False))
if violations or unknown:raise SystemExit(2)
