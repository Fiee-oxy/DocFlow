"""Read-only index audit; run inside retrieval container with a JSON configuration path."""
import json,math,sys
from pymilvus import MilvusClient
config=json.load(open(sys.argv[1],encoding='utf-8'));client=MilvusClient(uri='http://milvus:19530');collection='document_chunks_v1'
rows=client.get(collection_name=collection,ids=config['ids'],output_fields=['chunk_id','owner_id','doc_id','index_version','dense','content'],consistency_level='Strong')
actual={r['chunk_id']:r for r in rows}
invalid=[];wrong=[]
for key,row in actual.items():
    values=row['dense']
    if not all(math.isfinite(v) for v in values) or sum(v*v for v in values)<1e-10:invalid.append({'chunk_id':key,'pages':config['pages'].get(key),'finite':all(math.isfinite(v) for v in values)})
    if row['owner_id']!=config['owner'] or row['doc_id']!=config['doc_id'] or row['index_version']!=config['version']:wrong.append(key)
print(json.dumps({'expected':len(config['ids']),'present':len(rows),'missing':sorted(set(config['ids'])-set(actual)),'invalid_vectors':invalid,'scope_mismatches':wrong},allow_nan=False))
scope=f"owner_id == {config['owner']} and doc_id == {json.dumps(config['doc_id'])} and index_version == {json.dumps(config['version'])}"
hits=client.search(collection_name=collection,data=[rows[0]['dense']],anns_field='dense',filter=scope,limit=100,
                   output_fields=['chunk_id'],search_params={'metric_type':'COSINE'},consistency_level='Strong')[0]
ids={h['entity']['chunk_id'] for h in hits}
print(json.dumps({'filtered_count':client.query(collection_name=collection,filter=scope,output_fields=['count(*)'],consistency_level='Strong'),
                  'ann_count':len(hits),'not_ann_searchable':[{'id':key,'pages':config['pages'][key]} for key in sorted(set(config['ids'])-ids)]}))
for label,expr in [('owner',f"owner_id == {config['owner']}"),('document',f"doc_id == {json.dumps(config['doc_id'])}"),('version',f"index_version == {json.dumps(config['version'])}"),('pk',f"chunk_id in {json.dumps(config['ids'])}"),('pk_scope',f"chunk_id in {json.dumps(config['ids'])} and ({scope})")]:
    result=client.query(collection_name=collection,filter=expr,output_fields=['chunk_id'],limit=10000,consistency_level='Strong')
    print(json.dumps({'filter':label,'rows':len(result),'expected_intersection':len({r['chunk_id'] for r in result}&set(config['ids']))}))
for label,expr in [('in_scope',f"owner_id in [{config['owner']}] and doc_id in {json.dumps([config['doc_id']])} and index_version in {json.dumps([config['version']])}"),('reverse',f"index_version == {json.dumps(config['version'])} and doc_id == {json.dumps(config['doc_id'])} and owner_id == {config['owner']}"),('nested',f"owner_id == {config['owner']} and (doc_id == {json.dumps(config['doc_id'])} and index_version == {json.dumps(config['version'])})")]:
    found=client.query(collection_name=collection,filter=expr,output_fields=['chunk_id'],limit=10000,consistency_level='Strong')
    print(json.dumps({'filter':label,'rows':len(found),'expected_intersection':len({r['chunk_id'] for r in found}&set(config['ids']))}))
