"""Read-only compound-filter equivalence audit. Run inside the retrieval container."""
import json,sys
from pymilvus import MilvusClient
config=json.load(open(sys.argv[1],encoding='utf-8'));client=MilvusClient(uri='http://milvus:19530');rows=[]
for item in config:
    owner=item['ownerId'];doc=json.dumps([item['documentId']]);ver=json.dumps([item['version']]);expected=set(item['ids'])
    eq=f"owner_id == {owner} and doc_id == {json.dumps(item['documentId'])} and index_version == {json.dumps(item['version'])}"
    term=f"owner_id in [{owner}] and doc_id in {doc} and index_version in {ver}"
    def query(expr):return client.query(collection_name='document_chunks_v1',filter=expr,output_fields=['chunk_id','owner_id','doc_id','index_version'],limit=16384,consistency_level='Strong')
    old=query(eq);new=query(term);visible={r['chunk_id'] for r in new}
    scope_ok=all(r['owner_id']==owner and r['doc_id']==item['documentId'] and r['index_version']==item['version'] for r in new)
    rows.append({'document_key':item['key'],'expected':len(expected),'equality_rows':len(old),'in_rows':len(new),'in_exact_expected_ids':visible==expected,'scope_valid':scope_ok})
print(json.dumps({'documents':len(rows),'equality_mismatch_documents':sum(r['equality_rows']!=r['expected'] for r in rows),'in_exact_documents':sum(r['in_exact_expected_ids'] for r in rows),'scope_valid':all(r['scope_valid'] for r in rows),'details':rows}))
