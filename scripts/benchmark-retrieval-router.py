"""Actual BGE/Milvus/Reranker paired A/B on frozen chunks and questions; no chat LLM."""
import json
import sys
import time
import uuid
import hashlib
from pathlib import Path
sys.path.insert(0,"/retrieval")
import app as r
from retrieval_router import fuse
from evidence_metrics import coverage,source_order


def main():
    root,out=map(Path,sys.argv[1:3]);out.mkdir(exist_ok=True)
    manifestbytes=(root/"manifest.json").read_bytes();manifest=json.loads(manifestbytes)
    report=dict(llmCalls=0,productionWrites=0,scope="REAL_RETRIEVAL_FUSION_RERANK_NOT_JAVA_OR_CHAT",manifestSha256=hashlib.sha256(manifestbytes).hexdigest(),
        parameters=dict(dense=30,bm25=30,rrf=28,rerankTop=12,rerankTokens=r.RERANK_MAX_TOKENS),documents=[],queries=[])
    def save():(out/"results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    dimension=len(r.encode(["warmup"])[0]);r.rerank_scores("warmup",["warmup evidence"])
    report["embeddingDimension"]=dimension
    number=0
    for document in manifest["documents"]:
        chunks=json.loads((root/document["id"]/"legacy/chunks.json").read_text(encoding="utf-8"))
        identifier=str(uuid.uuid4());owner=991104;version="router-eval-v1"
        r.COLLECTION="router_eval_"+uuid.uuid4().hex[:16];collection=r.COLLECTION
        state=dict(document=document["id"],collection=collection,chunks=len(chunks),cleanup=False)
        report["documents"].append(state);save()
        try:
            start=time.perf_counter();r.ensure_collection(dimension)
            for offset in range(0,len(chunks),32):
                batch=chunks[offset:offset+32];vectors=r.encode([c["search_text"] for c in batch])
                rows=[{k:c[k] for k in ("chunk_id","section_id","content","search_text")} |
                    dict(doc_id=identifier,owner_id=owner,index_version=version,sources=json.dumps(c["sources"],ensure_ascii=False),dense=v)
                    for c,v in zip(batch,vectors)]
                r.milvus().upsert(collection_name=collection,data=rows)
            r.milvus().flush(collection_name=collection)
            state["indexMs"]=(time.perf_counter()-start)*1000
            order=source_order(chunks)
            def score(hits):return coverage([s for h in hits for s in h.get("sources",[])],case,order)
            # Exercise both indexes before measuring; no result or score cache.
            r.search(r.SearchRequest(owner_id=owner,scopes=[dict(document_id=identifier,index_version=version)],question="warmup",dense_top_k=30,sparse_top_k=30))
            for case in document["cases"]:
                row=dict(id=case["case_id"],document=document["id"],language=document["language"],variant=case["variant"],question=case["question"],arms={})
                request=r.RoutedRequest(owner_id=owner,scopes=[dict(document_id=identifier,index_version=version)],question=case["question"],dense_top_k=30,sparse_top_k=30)
                for arm in (("BASELINE","ROUTED") if number%2==0 else ("ROUTED","BASELINE")):
                    began=time.perf_counter()
                    if arm=="ROUTED":result=r.retrieve_routed(request)
                    else:
                        search=r.search(request);search_ms=(time.perf_counter()-began)*1000;start=time.perf_counter()
                        fused=fuse(search["dense"],search["sparse"]);fusion_ms=(time.perf_counter()-start)*1000;start=time.perf_counter()
                        ranked=r.rerank(r.RerankRequest(question=case["question"],candidates=fused))["candidates"]
                        result=dict(dense=search["dense"],sparse=search["sparse"],fused=fused,reranked=ranked,selected=ranked[:12],
                            timingMs=search.get("timingMs",{}) | dict(search=search_ms,rrf=fusion_ms,rerank=(time.perf_counter()-start)*1000,total=(time.perf_counter()-began)*1000))
                    result["observedTotalMs"]=(time.perf_counter()-began)*1000
                    result["coverage"]={"union":score(result["dense"]+result["sparse"]),"fused":score(result["fused"]),"top12":score(result["selected"])}
                    row["arms"][arm]=result
                report["queries"].append(row);number+=1;save()
                print(json.dumps(dict(case=row["id"],baselineMs=round(row["arms"]["BASELINE"]["observedTotalMs"]),routedMs=round(row["arms"]["ROUTED"]["observedTotalMs"]),routing=row["arms"]["ROUTED"]["routing"]),ensure_ascii=False),flush=True)
        finally:
            if r.milvus().has_collection(collection):r.milvus().drop_collection(collection)
            state["cleanup"]=not r.milvus().has_collection(collection);save()


if __name__=="__main__":main()
