"""Disposable two-document corpus for A/B. C uses frozen candidates independently."""
import json,os,re,sys,uuid,hashlib
from pathlib import Path
sys.path.insert(0,"/retrieval")
import app as r

if __name__=="__main__":
    root=Path("/work");base=root/"evaluation/chunk-expanded-20261019";out=root/"evaluation/three-experiments-20261105"
    if "three-milvus" not in os.environ.get("MILVUS_URI",""):raise RuntimeError("Disposable database required")
    manifest=json.loads((base/"manifest.json").read_text());dim=len(r.encode(["warmup"])[0]);r.rerank_scores("warmup",["warmup evidence"])
    r.COLLECTION="three_exp_"+uuid.uuid4().hex[:16];r.ensure_collection(dim);cases=[]
    for key in ("rfc9110","pbc-2024q4"):
        document=next(d for d in manifest["documents"] if d["id"]==key)
        chunks=json.loads((base/key/"legacy/chunks.json").read_text());doc=str(uuid.uuid4());owner=991105;version="three-exp-v1"
        for offset in range(0,len(chunks),32):
            batch=chunks[offset:offset+32];vectors=r.encode([c["search_text"] for c in batch])
            r.milvus().upsert(collection_name=r.COLLECTION,data=[{k:c[k] for k in ("chunk_id","section_id","content","search_text")} |
                dict(doc_id=doc,owner_id=owner,index_version=version,sources=json.dumps(c["sources"],ensure_ascii=False),dense=v) for c,v in zip(batch,vectors)])
        payload=dict(owner_id=owner,scopes=[dict(document_id=doc,index_version=version)],dense_top_k=30,sparse_top_k=30,fusion_top_k=28,final_top_k=12)
        for case in document["cases"]:
            if case["variant"]=="ORIGINAL":cases.append(dict(id=case["case_id"],group="NATURAL",language=document["language"],payload=payload|dict(question=case["question"]),case=case,order={c["chunk_id"]:i for i,c in enumerate(chunks)}))
        literal=None
        for c in chunks:
            for phrase in re.findall(r'[^\n"“”]{24,65}',c["content"]):
                if sum(phrase in x["content"] for x in chunks)==1:literal=phrase;break
            if literal:break
        cases.append(dict(id=key+"-literal",group="SOURCE_DERIVED_LOOKUP",language=document["language"],payload=payload|dict(question='Find exact "'+literal+'"' if document['language']=='en' else '定位“'+literal+'”的原文')))
        cases.append(dict(id=key+"-semantic",group="SEMANTIC_PROBE_NO_GOLD",language=document["language"],payload=payload|dict(question="Find text with similar meaning to stateless request processing" if document['language']=='en' else "查找语义相似的货币政策传导描述")))
    r.milvus().flush(collection_name=r.COLLECTION)
    (out/"ab-fixture.json").write_text(json.dumps(cases,ensure_ascii=False),encoding="utf-8")
    (out/"environment.json").write_text(json.dumps(dict(collection=r.COLLECTION,embedding=r.EMBEDDING,reranker=r.RERANKER,threads=2,cpuLimit=2,memoryLimitGiB=5,
        tokens=r.RERANK_MAX_TOKENS,batch=r.RERANK_BATCH_SIZE,queueTimeoutSeconds=r.QUEUE_TIMEOUT_SECONDS,cache="MODEL_WEIGHTS_ONLY",sourceHashes={p:hashlib.sha256((Path('/retrieval')/p).read_bytes()).hexdigest() for p in ('app.py','retrieval_router.py')}),indent=2),encoding="utf-8")
    print("THREE_EXPERIMENTS_READY",flush=True)
    import uvicorn
    uvicorn.run(r.app,host="0.0.0.0",port=5002,workers=1,limit_concurrency=8)
