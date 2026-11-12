"""Structural check on saved real Docling Word batches; no inference or writes."""
import json
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,'/retrieval')
import app
from transformers import AutoTokenizer

def main():
    root=Path('/work/evaluation/document-hierarchy-20261030');out=[]
    tokenizer=AutoTokenizer.from_pretrained('BAAI/bge-small-zh-v1.5')
    for key in ('en_cargo_bikes','zh_shiep_quality'):
        events=[json.loads(s) for s in (root/f'{key}-docling-events.jsonl').read_text().splitlines()]
        batches=[e for e in events if e['type']=='batch'];parents=[];children=[];nodes=[]
        for number,event in enumerate(batches):
            request=app.ChunkRequest(document_id=key,owner_id=7,index_version='hierarchy-structural-test',batch_index=number,batch=event,hierarchy_enabled=True)
            with patch.object(app,'embedding_models',return_value=(tokenizer,None)):
                bundle=app.chunk_bundle(request)
            parents.extend(bundle['parents']);children.extend(bundle['chunks']);nodes.extend(bundle['structure']['nodes'])
            assert all(c['token_count']<=512 for c in bundle['chunks'])
            assert all(p['token_count']<=1500 for p in bundle['parents'])
            assert len(bundle['structure']['nodes'])>=len(event['blocks'])
        row={'document':key,'source_batches':len(batches),'nodes':len(nodes),'parents':len(parents),'children':len(children),
             'embedding_max_tokens':max(c['token_count'] for c in children),'parent_max_tokens':max(p['token_count'] for p in parents)}
        if key=='en_cargo_bikes':
            capacity=next(c for c in children if '150kg' in c['content'])
            parent=next(p for p in parents if p['parent_id']==capacity['parent_id'])
            row['capacity_parent_also_has_vehicle']='replacing an electric van' in parent['content']
            row['capacity_parent_bytes']=len(json.dumps(parent['source'],ensure_ascii=False,separators=(',',':')).encode())
            subset=[dict(c,order=i) for i,c in enumerate(children) if c['chunk_id'] in parent['child_ids']]
            for i,child in enumerate(subset):child['order']=i
            referenced=set(parent['ast_node_ids']);ast=dict(bundle['structure'],nodes=[n for n in nodes if n['node_id'] in referenced])
            (root/'published-parent-fixture.json').write_text(json.dumps({'chunks':subset,'parents':[parent],'structure':ast},ensure_ascii=False),encoding='utf8')
        out.append(row)
        (root/f'{key}-bundle.json').write_text(json.dumps({'structure':bundle['structure'],'parents':parents,'chunks':children},ensure_ascii=False),encoding='utf8')
    (root/'real-structure-checks.json').write_text(json.dumps({'llmCalls':0,'embeddingCalls':0,'scope':'Saved Docling output + real BGE tokenizer; not retrieval quality A/B','results':out},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(out))
if __name__=='__main__':main()
