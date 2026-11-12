"""Separate literal-navigation probes from a real RFC. Not natural QA accuracy."""
import json,re,sys,time,uuid,unicodedata
from pathlib import Path
sys.path.insert(0,"/retrieval")
import app as r
from retrieval_router import plan,fuse


def main():
    fixture,out=map(Path,sys.argv[1:3]);chunks=json.loads((fixture/"rfc9110/legacy/chunks.json").read_text(encoding="utf-8"))
    norm=lambda text:unicodedata.normalize("NFKC",text).casefold()
    probes=[]
    for begin in (0,len(chunks)//2):
        for c in chunks[begin:]:
            words=re.findall(r"\b[a-zA-Z]{3,}\b(?:[ ,/-]+[a-zA-Z]{3,}\b){5,9}",c["content"])
            found=False
            for phrase in words:
                if not 35<=len(phrase)<=90:continue
                question=f'Find "{phrase}" in the document.'
                if plan(question)["initialRoute"]!="BM25":continue
                if sum(norm(phrase) in norm(x["content"]) for x in chunks)!=1:continue
                probes.append(dict(question=question,phrase=phrase,expectedChunk=c["chunk_id"]));found=True;break
            if found:break
    assert len(probes)==2
    dim=len(r.encode(["warmup"])[0]);r.rerank_scores("warmup",["warmup evidence"])
    r.COLLECTION="router_probe_"+uuid.uuid4().hex[:16];owner=991104;doc=str(uuid.uuid4());version="nav-v1"
    report=dict(scope="SOURCE_DERIVED_LITERAL_NAVIGATION_NOT_NATURAL_QA",llmCalls=0,probes=probes,results=[],cleanup=False)
    def save():(out/"navigation-probes.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    try:
        r.ensure_collection(dim)
        for offset in range(0,len(chunks),32):
            batch=chunks[offset:offset+32];vectors=r.encode([c["search_text"] for c in batch])
            r.milvus().upsert(collection_name=r.COLLECTION,data=[{k:c[k] for k in ("chunk_id","section_id","content","search_text")} |
                dict(doc_id=doc,owner_id=owner,index_version=version,sources=json.dumps(c["sources"],ensure_ascii=False),dense=v) for c,v in zip(batch,vectors)])
        r.milvus().flush(collection_name=r.COLLECTION)
        for number,p in enumerate(probes):
            row=dict(question=p["question"],expectedChunk=p["expectedChunk"],arms={})
            request=r.RoutedRequest(owner_id=owner,scopes=[dict(document_id=doc,index_version=version)],question=p["question"],dense_top_k=30,sparse_top_k=30)
            for arm in (("BASELINE","ROUTED") if number==0 else ("ROUTED","BASELINE")):
                start=time.perf_counter()
                if arm=="ROUTED":result=r.retrieve_routed(request);selected=result["selected"];routing=result["routing"]
                else:
                    search=r.search(request);fused=fuse(search["dense"],search["sparse"])
                    selected=r.rerank(r.RerankRequest(question=p["question"],candidates=fused))["candidates"][:12];routing=None
                row["arms"][arm]=dict(totalMs=(time.perf_counter()-start)*1000,expectedFound=any(c["chunk_id"]==p["expectedChunk"] for c in selected),routing=routing)
            report["results"].append(row);save();print(json.dumps(row,ensure_ascii=False),flush=True)
    finally:
        if r.milvus().has_collection(r.COLLECTION):r.milvus().drop_collection(r.COLLECTION)
        report["cleanup"]=True;save()


if __name__=="__main__":main()
