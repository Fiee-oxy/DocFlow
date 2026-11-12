"""Live BGE/Milvus/Reranker integration, synthetic corpus, NO mocked retrieval or LLM."""
import json
import uuid
import urllib.request
from pymilvus import MilvusClient
from docling_core.types.doc import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel
import os

base=os.getenv("RETRIEVAL_TEST_URL","http://127.0.0.1:5002")
owner=987654321
docs=[str(uuid.uuid4()),str(uuid.uuid4())]
version="integration-fixture-v1"
def call(path,body):
    request=urllib.request.Request(base+path,data=json.dumps(body,ensure_ascii=False).encode(),headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(request,timeout=180).read())
def index(doc,items,batch=0,ver=version,docling=None):
    payload={"blocks":[{"text":text,"section":section,"ref":f"#/texts/{i}","pages":[page],"label":"text"} for i,(text,section,page) in enumerate(items)]}
    if docling:payload["doclingDocument"]=docling
    return call("/index",{"document_id":doc,"owner_id":owner,"index_version":ver,"batch_index":batch,"batch":payload})
def search(doc,q,sections=None,who=owner,ver=version):
    return call("/search",{"owner_id":who,"scopes":[{"document_id":doc,"index_version":ver}],"question":q,"section_ids":sections or []})
client=MilvusClient(uri=os.getenv("MILVUS_URI","http://milvus:19530"))
checks=[]
try:
    a=index(docs[0],[("Redis 令牌桶限流用于抑制突发请求，保护后端服务。","第一章 限流",1),
                      ("RocketMQ 将耗时任务异步处理，避免接口阻塞。","第二章 异步处理",2)])
    b=index(docs[1],[("本系统使用漏桶算法平滑流量，与令牌桶允许突发流量的设计不同。","第一章 限流",3)])
    assert a["chunks"] and b["chunks"];checks.append("real_dense_and_bm25_index")
    exact=search(docs[0],"RocketMQ")
    assert any("RocketMQ" in h["content"] for h in exact["sparse"]);checks.append("bm25_exact_term")
    semantic=search(docs[0],"如何防止瞬间大量访问压垮接口")
    print(json.dumps({"semantic_dense": [{"content":h["content"],"score":h["score"]} for h in semantic["dense"]]},ensure_ascii=False))
    assert any("令牌桶" in h["content"] for h in semantic["dense"]);checks.append("bge_semantic_candidate_recall")
    candidates=list({h["chunk_id"]:h for h in semantic["dense"]+semantic["sparse"]}.values())
    ranked=call("/rerank",{"question":"如何防止瞬间大量访问压垮接口","candidates":candidates})["candidates"]
    assert ranked and all("rerank_score" in h for h in ranked);checks.append("real_bge_rerank_execution")
    hard_case_ok="令牌桶" in ranked[0]["content"]
    print(json.dumps({"quality_observation":{"hard_case_top1_correct":hard_case_ok,
          "reranked":[{"content":h["content"],"score":h["rerank_score"]} for h in ranked]}},ensure_ascii=False))
    section=a["chunks"][1]["section_id"]
    restricted=search(docs[0],"Redis RocketMQ",[section])
    assert all(h["section_id"]==section for h in restricted["dense"]+restricted["sparse"]);checks.append("section_filter_before_search")
    unauthorized=search(docs[0],"Redis",who=owner+1)
    assert not unauthorized["dense"] and not unauthorized["sparse"];checks.append("owner_isolation")
    # A second version must never contaminate the selected version.
    index(docs[0],[("新版专属秘密标记 NewVersionOnly。","第三章 新版",4)],ver="integration-fixture-v2")
    old=search(docs[0],"NewVersionOnly")
    assert all(h["index_version"]==version for h in old["dense"]+old["sparse"]);checks.append("version_isolation")
    joint=[]
    for doc in docs:
        hits=search(doc,"缓存限流方案")
        joint.extend(hits["dense"])
    assert set(h["doc_id"] for h in joint)==set(docs);checks.append("independent_multi_document_recall")
    # True DoclingDocument -> actual HybridChunker, not text fallback.
    document=DoclingDocument(name="HybridChunker fixture")
    document.add_heading(text="性能优化",level=1)
    document.add_text(text="通过线程池隔离索引和问答任务，限制任务并发数量。",label=DocItemLabel.TEXT)
    hybrid=index(docs[1],[],batch=1,docling=document.export_to_dict())
    assert hybrid["chunking"]=="DOCLING_HYBRID" and hybrid["chunks"];checks.append("actual_docling_hybrid_chunker")
    # Retrying exactly the same batch must upsert identical primary keys.
    repeat=index(docs[1],[("本系统使用漏桶算法平滑流量，与令牌桶允许突发流量的设计不同。","第一章 限流",3)])
    assert repeat["chunks"][0]["chunk_id"]==b["chunks"][0]["chunk_id"];checks.append("idempotent_index_ids")
    print(json.dumps({"passed":checks,"count":len(checks),"llm_calls":0,
                      "quality_limitations":[] if hard_case_ok else ["Paraphrased burst-traffic question has incorrect Top-1; model quality not accepted for this case"]},ensure_ascii=False))
finally:
    client.delete(collection_name="document_chunks_v1",filter="owner_id == "+str(owner))
