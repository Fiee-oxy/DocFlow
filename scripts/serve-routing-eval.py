"""Dedicated disposable Milvus collection and warm real model HTTP service."""
import json,os,re,sys,uuid
from pathlib import Path
sys.path.insert(0,"/retrieval")
import app as r

if __name__=="__main__":
    chunks=json.loads(Path(sys.argv[1]).read_text());output=Path(sys.argv[2]);output.mkdir(exist_ok=True)
    if "routing-v2-milvus" not in os.environ.get("MILVUS_URI",""):raise RuntimeError("Requires disposable evaluation database")
    dimension=len(r.encode(["warmup"])[0]);r.rerank_scores("warmup",["warmup evidence"])
    r.COLLECTION="routing_v2_"+uuid.uuid4().hex[:16];doc=str(uuid.uuid4());owner=991105;version="routing-v2-eval"
    r.ensure_collection(dimension)
    for offset in range(0,len(chunks),32):
        batch=chunks[offset:offset+32];vectors=r.encode([c["search_text"] for c in batch])
        rows=[{k:c[k] for k in ("chunk_id","section_id","content","search_text")} | dict(doc_id=doc,owner_id=owner,index_version=version,sources=json.dumps(c["sources"],ensure_ascii=False),dense=v) for c,v in zip(batch,vectors)]
        r.milvus().upsert(collection_name=r.COLLECTION,data=rows)
    r.milvus().flush(collection_name=r.COLLECTION)
    payload=dict(owner_id=owner,scopes=[dict(document_id=doc,index_version=version)],dense_top_k=30,sparse_top_k=30,fusion_top_k=28,final_top_k=12)
    queries=[("natural","What does HTTP 403 mean?"),("literal",'Find exact "self-descriptive messages"'),
             ("semantic","Find text with similar meaning to stateless request processing"),("empty","搜索包含‘zqxvkjp9876543210’的原文")]
    (output/"workload.json").write_text(json.dumps([dict(id=key,payload=payload|dict(question=q)) for key,q in queries],ensure_ascii=False,indent=2),encoding="utf-8")
    (output/"service-environment.json").write_text(json.dumps(dict(collection=r.COLLECTION,chunks=len(chunks),embedding=r.EMBEDDING,reranker=r.RERANKER,embeddingDimension=dimension,
        topK=dict(dense=30,bm25=30,fusion=28,final=12),rerankTokens=r.RERANK_MAX_TOKENS,rerankBatch=r.RERANK_BATCH_SIZE,cache="MODEL_WEIGHTS_ONLY_NO_RESULT_CACHE"),indent=2),encoding="utf-8")
    print("EVAL_SERVICE_READY",flush=True)
    import uvicorn
    uvicorn.run(r.app,host="0.0.0.0",port=5002,workers=1,limit_concurrency=8)
