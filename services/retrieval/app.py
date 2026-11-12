"""Internal, local-only BGE + Docling HybridChunker + shared Milvus adapter."""
import hashlib
import os
import threading
import time
from contextvars import ContextVar
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from chunk_strategies import STRATEGIES, VERSIONS
from pathlib import Path
from query_expansion import QueryExpander, contains

query_expander = QueryExpander(os.getenv("SYNONYM_DICTIONARY_PATH", str(Path(__file__).with_name("synonyms.json"))),
    enabled=os.getenv("SYNONYM_EXPANSION_ENABLED", "false").lower() == "true",
    weight=float(os.getenv("SYNONYM_ROUTE_WEIGHT", "0.25")),
    max_terms=int(os.getenv("SYNONYM_MAX_TERMS", "6")))

app = FastAPI(title="Adaptive document retrieval")
@app.exception_handler(Exception)
async def classified_retrieval_error(request,error):
    from fastapi.responses import JSONResponse
    name=type(error).__name__.lower()
    kind="RETRIEVAL_TIMEOUT" if isinstance(error,TimeoutError) or "timeout" in name else "RETRIEVAL_UNAVAILABLE"
    return JSONResponse(status_code=504 if kind=="RETRIEVAL_TIMEOUT" else 503,content={"detail":{"failureType":kind}})
@app.middleware("http")
async def private_service_auth(request,call_next):
    arrived=time.perf_counter()
    import hmac
    from fastapi.responses import JSONResponse
    key=os.getenv("RETRIEVAL_API_KEY","")
    if key and not hmac.compare_digest(request.headers.get("authorization",""),"Bearer "+key):
        return JSONResponse(status_code=401,content={"detail":"Unauthorized internal service request"})
    response=await call_next(request)
    response.headers["X-Retrieval-Server-Elapsed-Ms"]=f"{(time.perf_counter()-arrived)*1000:.3f}"
    return response
gate = threading.BoundedSemaphore(1)
models = {}
COLLECTION = os.getenv("MILVUS_COLLECTION", "document_chunks_v1")
EMBEDDING = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
RERANKER = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-base")
RERANK_BATCH_SIZE = max(1, min(16, int(os.getenv("RERANK_BATCH_SIZE", "8"))))
RERANK_MAX_TOKENS = max(64, min(512, int(os.getenv("RERANK_MAX_TOKENS", "256"))))
# The Java query executor is deliberately bounded. Let its two workers wait
# for this single inference slot instead of turning normal contention into
# failed durable tasks. This does not increase model concurrency or memory.
QUEUE_TIMEOUT_SECONDS = max(.01,min(30,float(os.getenv("RETRIEVAL_QUEUE_TIMEOUT_SECONDS","30"))))
_score_timing = ContextVar("rerank_score_timing",default=None)

@contextmanager
def bounded(timing=None,stage="workerQueue"):
    started=time.perf_counter();acquired=gate.acquire(timeout=QUEUE_TIMEOUT_SECONDS)
    elapsed=(time.perf_counter()-started)*1000
    if timing is not None:timing[stage]=elapsed
    if not acquired:
        raise HTTPException(429, detail={"failureType":"RETRIEVAL_BUSY","phase":stage,"timingMs":{stage:elapsed}})
    try:
        yield
    finally:
        gate.release()

def hash_text(text):
    return hashlib.sha256(text.encode()).hexdigest()

def embedding_models():
    if "embedding" not in models:
        import torch
        from transformers import AutoTokenizer, AutoModel
        torch.set_num_threads(int(os.getenv("OMP_NUM_THREADS", "2")))
        tokenizer = AutoTokenizer.from_pretrained(EMBEDDING)
        model = AutoModel.from_pretrained(EMBEDDING).eval()
        models["embedding"] = (tokenizer, model)
    return models["embedding"]

def encode(texts, query=False):
    import torch
    tokenizer, model = embedding_models()
    result = []
    for start in range(0, len(texts), 8):
        batch = texts[start:start+8]
        if query:
            batch = ["为这个句子生成表示以用于检索相关文章：" + t for t in batch]
        inputs = tokenizer(batch, padding=True, truncation=True, max_length=512, return_tensors="pt")
        with torch.inference_mode():
            vector = model(**inputs).last_hidden_state[:, 0]
            vector = torch.nn.functional.normalize(vector, p=2, dim=1)
        result.extend(vector.tolist())
    return result

def milvus():
    from pymilvus import MilvusClient
    if "milvus" not in models:
        models["milvus"] = MilvusClient(uri=os.getenv("MILVUS_URI", "http://localhost:19530"))
    return models["milvus"]

def ensure_collection(dim):
    from pymilvus import DataType, Function, FunctionType
    client = milvus()
    if client.has_collection(COLLECTION):
        return
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    for name, length in [("chunk_id",64),("doc_id",36),("index_version",64),("section_id",64),
                         ("content",65535),("search_text",65535),("sources",65535)]:
        kwargs = {"max_length": length}
        if name == "chunk_id": kwargs["is_primary"] = True
        if name == "search_text": kwargs.update(enable_analyzer=True, analyzer_params={"type":"chinese"})
        schema.add_field(field_name=name, datatype=DataType.VARCHAR, **kwargs)
    schema.add_field(field_name="owner_id", datatype=DataType.INT64)
    schema.add_field(field_name="dense", datatype=DataType.FLOAT_VECTOR, dim=dim)
    schema.add_field(field_name="sparse", datatype=DataType.SPARSE_FLOAT_VECTOR)
    schema.add_function(Function(name="bm25",input_field_names=["search_text"],output_field_names=["sparse"],function_type=FunctionType.BM25))
    indexes = client.prepare_index_params()
    indexes.add_index(field_name="dense",index_type="FLAT",metric_type="COSINE")
    indexes.add_index(field_name="sparse",index_type="SPARSE_INVERTED_INDEX",metric_type="BM25")
    client.create_collection(collection_name=COLLECTION,schema=schema,index_params=indexes,consistency_level="Strong")

class ChunkRequest(BaseModel):
    document_id: str
    owner_id: int
    index_version: str
    batch_index: int
    batch: dict
    max_tokens: int = Field(default=480, ge=32, le=480)
    target_tokens: int = Field(default=350, ge=32, le=480)
    min_preferred_tokens: int = Field(default=80,ge=1,le=480)
    merge_peers: bool = True
    strategy: Literal["legacy", "boundary"] = "legacy"
    strategy_revision: str | None = None
    hierarchy_enabled: bool = False
    parent_target_tokens: int = Field(default=1000,ge=480,le=2000)
    parent_max_tokens: int = Field(default=1500,ge=480,le=3000)

def _leaf_chunks(request):
    if request.strategy_revision and request.strategy_revision != VERSIONS[request.strategy]:
        raise HTTPException(409,"Chunk strategy revision changed; build a new index version")
    tokenizer, _ = embedding_models()
    batch = request.batch
    raw = []
    if request.strategy == "boundary":
        for item in batch.get("blocks", []):
            heading = item.get("section", "??")
            try:
                for piece in STRATEGIES[request.strategy](item.get("text", ""), heading, tokenizer, request.target_tokens, request.max_tokens):
                    if piece.strip(): raw.append((piece, heading, [item.get("ref", "")], item.get("pages", [])))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
    elif batch.get("chunkingDocument") is not None or batch.get("doclingDocument"):
        from docling_core.types.doc import DoclingDocument
        from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
        from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
        document = DoclingDocument.model_validate(batch.get("chunkingDocument", batch.get("doclingDocument")))
        # Keep structure boundaries first; our final merge checks the complete inherited title path.
        chunker = HybridChunker(tokenizer=HuggingFaceTokenizer(tokenizer=tokenizer, max_tokens=min(request.target_tokens,request.max_tokens)), merge_peers=False)
        for c in chunker.chunk(dl_doc=document):
            headings = list(c.meta.headings or [])
            refs = [i.self_ref for i in c.meta.doc_items]
            pages = sorted({p.page_no for i in c.meta.doc_items for p in i.prov})
            inherited = next((i.get("section") for i in batch.get("blocks",[]) if i.get("ref") in refs),None)
            raw.append((c.text,inherited or " / ".join(headings) or "正文",refs,pages))
    else:
        # Native FAST/legacy text has no DoclingDocument; explicitly use token-aware text fallback.
        for item in batch.get("blocks", []):
            text = item.get("text", "")
            heading = item.get("section", "正文")
            reserve = len(tokenizer.encode(heading,add_special_tokens=False))+8
            limit = max(16,min(request.target_tokens,request.max_tokens-reserve))
            # Offsets preserve the original text exactly; decoding token IDs can alter whitespace.
            encoded = tokenizer(text,add_special_tokens=False,return_offsets_mapping=True)
            offsets = encoded["offset_mapping"]
            char_start = 0
            for pos in range(0,len(offsets),limit):
                next_pos = min(pos+limit,len(offsets))
                char_end = offsets[next_pos][0] if next_pos < len(offsets) else len(text)
                piece = text[char_start:char_end]
                if piece.strip(): raw.append((piece,heading,[item.get("ref","")],item.get("pages",[])))
                char_start = char_end
    if request.merge_peers:
        merged=[]
        for content,heading,refs,pages in raw:
            if merged and merged[-1][1]==heading:
                previous=merged[-1]
                count=len(tokenizer.encode(content,add_special_tokens=False))
                joined=previous[0]+"\n"+content
                if count<request.min_preferred_tokens and len(tokenizer.encode(heading+"\n"+joined,add_special_tokens=True))<=request.max_tokens:
                    merged[-1]=(joined,heading,list(dict.fromkeys(previous[2]+refs)),sorted(set(previous[3]+pages)));continue
            merged.append((content,heading,refs,pages))
        raw=merged
    result = []
    import json
    source_blocks = {block.get("ref"): block for block in batch.get("blocks", [])}
    config = {k:getattr(request,k) for k in ("strategy","max_tokens","target_tokens","min_preferred_tokens","merge_peers")}
    fingerprint = hash_text(json.dumps(config,sort_keys=True)+VERSIONS[request.strategy])[:16]
    for order,(content,heading,refs,pages) in enumerate(raw):
        search = heading + "\n" + content
        # Reject rather than silently truncate metadata-enriched embedding input.
        count = len(tokenizer.encode(search,add_special_tokens=True))
        if count > 512:
            raise HTTPException(422,"Chunk plus heading exceeds embedding context; shorten title path")
        cid = hash_text(f"{request.document_id}:{request.index_version}:{request.batch_index}:{order}:{content}")
        if request.strategy != "legacy": cid = hash_text(cid+fingerprint)
        source = {"id":cid,"ref": f"b{request.batch_index}:" + ", ".join(refs),"pages":pages,
                  "batch_index":request.batch_index,"text":content,"section":heading}
        fragments = []
        seen = set()
        for ref in refs:
            for fragment in source_blocks.get(ref, {}).get("source_fragments", []):
                marker = fragment.get("origin_id") or json.dumps([fragment.get("batch_index"),fragment.get("ref"),fragment.get("pages")])
                if marker in seen:
                    continue
                seen.add(marker)
                # Full originals/cells live in MySQL parser payloads, not every vector row.
                fragments.append({k:fragment[k] for k in ("origin_id","ref","batch_index","pages","bbox","page_size",
                                                         "node_ref","body_node","parse_unit","source_part","part_kind","content_kind") if k in fragment})
        if fragments:
            source["fragments"] = fragments
            source["ref"] = ", ".join(f"b{f.get('batch_index',request.batch_index)}:{f.get('ref','')}" for f in fragments)
        result.append({"strategy":request.strategy,"strategy_version":VERSIONS[request.strategy],"config_fingerprint":fingerprint,"chunk_id":cid,"section_id":hash_text(heading),"order":order,"content":content,
                       "search_text":search,"token_count":count,"title_path":heading,
                       "page_start":min(pages) if pages else None,"page_end":max(pages) if pages else None,
                       "sources":[source]})
        if request.hierarchy_enabled:result[-1]['source_refs']=refs
    return result

def chunk_bundle(request):
    chunks=_leaf_chunks(request)
    if not request.hierarchy_enabled:return {'chunks':chunks}
    if request.parent_target_tokens>request.parent_max_tokens:
        raise HTTPException(422,'Parent target exceeds maximum')
    from document_structure import bundle_for
    try:return bundle_for(request,chunks,embedding_models()[0])
    except ValueError as error:raise HTTPException(422,str(error)) from error

def chunks_for(request):
    return chunk_bundle(request)['chunks']

@app.post('/structure/preview')
def structure_preview(request: ChunkRequest):
    from document_structure import ast_for
    try:return {'structure':ast_for(request),'writes':0,'llmCalls':0}
    except ValueError as error:raise HTTPException(422,str(error)) from error

@app.get("/chunk/strategies")
def chunk_strategies():
    return {"default":"legacy","strategies":VERSIONS,"parameters":ChunkRequest.model_json_schema()}

@app.post("/chunk/preview")
def chunk_preview(request: ChunkRequest):
    with bounded():
        bundle=chunk_bundle(request)
        return bundle|{"strategy":request.strategy,"count":len(bundle['chunks']),"writes":0,"llmCalls":0}

@app.get("/health")
def health():
    import time,gc,resource
    return {"status":"UP","embedding_model":EMBEDDING,"reranker_model":RERANKER,
            "continuityDocument": True,
            "rerank_batch_size":RERANK_BATCH_SIZE,"rerank_max_tokens":RERANK_MAX_TOKENS,
            "models_loaded":"embedding" in models,"collection":COLLECTION,
            "synonyms":query_expander.expand(""),
            "resources":{"cpuSeconds":time.process_time(),"peakRssKiB":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"gcCounts":gc.get_count()}}

class ExpansionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    include_routing_policy: bool = False

@app.post("/query/expand")
def expand_query(request: ExpansionRequest):
    result=query_expander.expand(request.question)
    if request.include_routing_policy:
        from retrieval_router import REVISION,QUALITY_REVISION,rules
        result=result|{"routing_policy":{"version":REVISION,"dictionaryVersion":rules().sha,"qualityVersion":QUALITY_REVISION}}
    return result

@app.post("/warmup")
def warmup():
    with bounded():
        vector = encode(["模型加载验证"])[0]
        ensure_collection(len(vector))
        rerank_scores("性能优化",["优化线程池并发数"])
        return {"dimension":len(vector),"ready":True}

@app.post("/index")
def index(request: ChunkRequest):
    import json
    with bounded():
        bundle=chunk_bundle(request);chunks=bundle['chunks']
        if not chunks:return bundle
        vectors = encode([c["search_text"] for c in chunks])
        ensure_collection(len(vectors[0]))
        for start in range(0,len(chunks),32):
            data = []
            for c,vector in zip(chunks[start:start+32],vectors[start:start+32]):
                data.append({k:c[k] for k in ["chunk_id","section_id","content","search_text"]} |
                            {"doc_id":request.document_id,"owner_id":request.owner_id,"index_version":request.index_version,
                             "sources":json.dumps(c["sources"],ensure_ascii=False),"dense":vector})
            milvus().upsert(collection_name=COLLECTION,data=data)
        # Strong read validates the exact IDs before Java publishes this batch watermark.
        ids = [c["chunk_id"] for c in chunks]
        found = milvus().get(collection_name=COLLECTION,ids=ids,output_fields=["chunk_id"],consistency_level="Strong")
        if len(found) != len(ids): raise HTTPException(503,"Index batch visibility check failed")
        visible=milvus().query(collection_name=COLLECTION,filter=
            f"chunk_id in {json.dumps(ids)} and owner_id in [{request.owner_id}] and doc_id in {json.dumps([request.document_id])} and index_version in {json.dumps([request.index_version])}",
            output_fields=["chunk_id"],limit=len(ids),consistency_level="Strong")
        if {r['chunk_id'] for r in visible}!=set(ids):raise HTTPException(503,"Scoped index batch visibility check failed")
        return bundle|{"chunking":"BOUNDARY_V1" if request.strategy == "boundary" else "DOCLING_HYBRID" if request.batch.get("doclingDocument") else "TOKEN_TEXT_FALLBACK"}

class SearchRequest(BaseModel):
    owner_id: int
    scopes: list[dict]
    question: str = Field(min_length=1,max_length=2000)
    dense_top_k: int = Field(default=20,ge=1,le=100)
    sparse_top_k: int = Field(default=20,ge=1,le=100)
    section_ids: list[str] = []
    retrieval_mode: Literal["HYBRID","BM25","DENSE"] = "HYBRID"

def keyword_query(question):
    """Query-only lexical view; no business dictionaries, answer values or LLM calls."""
    import re, unicodedata
    text=unicodedata.normalize("NFKC",question).lower()
    stop=set("what which how many much is are was were the a an of in on to for and or does do did please according document report".split())
    words=[]
    for segment in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+",text):
        if segment.isascii():
            if segment not in stop and len(segment)>1: words.append(segment)
        else:
            # Overlapping character terms preserve attributes in languages without whitespace.
            words.extend(segment[i:i+2] for i in range(len(segment)-1))
    return " ".join(dict.fromkeys(words))[:2000]

def merge_lexical_routes(routes, limit, weights=None, reserve_last=0):
    scores={};hits={}
    weights=weights or [1.0]*len(routes)
    if len(weights)!=len(routes):raise ValueError("Route weights mismatch")
    for route,weight in zip(routes,weights):
        for rank,hit in enumerate(route,1):
            key=hit["chunk_id"];hits.setdefault(key,hit)
            scores[key]=scores.get(key,0)+weight/(60+rank)
    order=sorted(hits,key=lambda k:(-scores[k],k))
    selected=set()
    if reserve_last and len(routes)>1:
        baseline={h["chunk_id"] for route in routes[:-1] for h in route}
        # A weak auxiliary route otherwise cannot admit new evidence into a
        # full pool. Reserve at most two slots, protecting original top hits.
        for route in routes[:-1]:selected.update(h["chunk_id"] for h in route[:min(5,limit//4)])
        novel=[h["chunk_id"] for h in routes[-1] if h["chunk_id"] not in baseline]
        selected.update(novel[:min(2,reserve_last,limit//5)])
    for key in order:
        if len(selected)>=limit:break
        selected.add(key)
    return [hits[key]|{"lexical_rrf_score":scores[key]} for key in order if key in selected]

def scope_filter(request):
    import json
    if not request.scopes or len(request.scopes)>8: raise HTTPException(400,"Invalid document scope")
    # Equivalent single-value IN avoids reproduced compound-equality false negatives
    # on this Milvus instance. Every owner/document/version constraint remains mandatory.
    scopes = [f'(doc_id in {json.dumps([s["document_id"]])} and index_version in {json.dumps([s["index_version"]])})' for s in request.scopes]
    result = f"owner_id in [{int(request.owner_id)}] and (" + " or ".join(scopes) + ")"
    if request.section_ids: result += " and section_id in " + json.dumps(request.section_ids)
    return result

@app.post("/search")
def search(request: SearchRequest):
    import json
    queue_timing={}
    with bounded(queue_timing,"searchQueue"):
        client=milvus()
        if not client.has_collection(COLLECTION):raise HTTPException(409,detail={"failureType":"INDEX_NOT_READY"})
        scope=scope_filter(request)
        fields=["chunk_id","doc_id","index_version","section_id","content","search_text","sources"]
        def find(field,data,limit,metric):
            hits=client.search(collection_name=COLLECTION,data=data,anns_field=field,filter=scope,limit=limit,
                               output_fields=fields,search_params={"metric_type":metric},consistency_level="Strong")[0]
            result=[]
            for hit in hits:
                entity=hit["entity"]; entity["sources"]=json.loads(entity["sources"])
                entity["score"]=hit["distance"];result.append(entity)
            return result
        import time
        began=time.perf_counter()
        embedding_ms=0;dense_search_ms=0
        if request.retrieval_mode!="BM25":
            try:vector=encode([request.question],query=True)
            except Exception as error:raise HTTPException(503,detail={"failureType":"QUERY_EMBEDDING_FAILED"}) from error
            embedding_ms=(time.perf_counter()-began)*1000;dense_start=time.perf_counter()
            dense=find("dense",vector,request.dense_top_k,"COSINE");dense_search_ms=(time.perf_counter()-dense_start)*1000
        else:dense=[]
        dense_ms=(time.perf_counter()-began)*1000;began=time.perf_counter()
        if request.retrieval_mode=="DENSE":
            return {"dense":dense,"sparse":[],"timingMs":queue_timing|{"dense":dense_ms,"embedding":embedding_ms,"denseSearch":dense_search_ms,"bm25":0}}
        sparse=find("sparse",[request.question],request.sparse_top_k,"BM25")
        keywords=keyword_query(request.question)
        routes=[sparse]
        if keywords and keywords!=request.question:
            routes.append(find("sparse",[keywords],request.sparse_top_k,"BM25"))
        expansion=query_expander.expand(request.question)
        weights=[1.0]*len(routes)
        if expansion["terms"]:
            extra=find("sparse",[expansion["expanded_query"]],request.sparse_top_k,"BM25")
            routes.append([hit for hit in extra if any(contains(hit.get("search_text") or hit["content"],term) for term in expansion["terms"])])
            weights.append(expansion["route_weight"])
        sparse=merge_lexical_routes(routes,request.sparse_top_k,weights,reserve_last=2 if expansion["terms"] else 0)
        return {"dense":dense,"sparse":sparse,"keyword_query":keywords,"query_expansion":expansion,
                "timingMs":queue_timing|{"dense":dense_ms,"embedding":embedding_ms,"denseSearch":dense_search_ms,"bm25":(time.perf_counter()-began)*1000}}


class RoutedRequest(SearchRequest):
    query_variants: list[str] = Field(default_factory=list,max_length=2)
    rerank_question: str = Field(default="",max_length=2000)
    title_boost: bool = True
    focus_query: str = Field(default="",max_length=2000)
    fusion_top_k: int = Field(default=28,ge=10,le=100)
    final_top_k: int = Field(default=12,ge=1,le=100)
    rrf_k: int = Field(default=60,ge=1,le=1000)
    fallback_enabled: bool = True
    fallback_on_empty: bool = True
    fallback_on_low_confidence: bool = True
    bm25_min_coverage: float = Field(default=.55,ge=0,le=1,allow_inf_nan=False)
    dense_min_score: float = Field(default=.45,ge=-1,le=1,allow_inf_nan=False)
    quality_top_k: int = Field(default=5,ge=1,le=20)
    rerank_enabled: bool = True
    execution_mode: Literal["BASELINE","ROUTED","BM25_ONLY","DENSE_ONLY"] = "ROUTED"
    rules_enabled: dict[str,bool] = {}
    request_id: str = Field(default="",max_length=64)
    index_ready: bool = True
    page_range: list[int] = []


class RoutePreviewRequest(BaseModel):
    question:str=Field(min_length=1,max_length=2000)
    context:dict={}
    rules_enabled:dict[str,bool]={}


@app.post("/query/route")
def preview_route(request:RoutePreviewRequest):
    from retrieval_router import plan
    return plan(request.question,request.context,request.rules_enabled)


@app.post("/retrieve-routed")
def retrieve_routed(request:RoutedRequest):
    import time
    from retrieval_router import plan,assess,fuse,FallbackPolicy
    intent_question=request.rerank_question or request.question
    if any(not q.strip() or len(q)>600 for q in request.query_variants):raise HTTPException(422,detail={"failureType":"INVALID_QUERY_VARIANT"})
    if not request.index_ready:raise HTTPException(409,detail={"failureType":"INDEX_NOT_READY"})
    if request.page_range:raise HTTPException(422,detail={"failureType":"PAGE_SCOPE_NOT_INDEXED","message":"Use authorized source-page reading; this index has no page filter columns"})
    began=time.perf_counter()
    if request.execution_mode=="ROUTED":
        decision=plan(intent_question,{"documentGrounded":True,"documentIds":[s.get("document_id") for s in request.scopes],
            "scope":{"documents":request.scopes,"sectionIds":request.section_ids}},request.rules_enabled);route=decision["initialRoute"]
    else:
        from retrieval_router import REVISION
        route={"BASELINE":"HYBRID","BM25_ONLY":"BM25","DENSE_ONLY":"DENSE"}[request.execution_mode]
        decision=dict(revision=REVISION,ruleVersion=REVISION,mode=route,reasonCode="FIXED_"+request.execution_mode,matchedRules=[],timingMs=dict(analyzer=0,ruleEngine=0))
    first=search(request.model_copy(update={"retrieval_mode":route}))
    dense,sparse=first["dense"],first["sparse"];timings=dict(first.get("timingMs",{}))
    confidence=assess(intent_question,sparse if route=="BM25" else dense,route,
        bm25_min_coverage=request.bm25_min_coverage,dense_min_score=request.dense_min_score,
        quality_top_k=request.quality_top_k) if route!="HYBRID" else {}
    other=FallbackPolicy(request.fallback_enabled and request.execution_mode=="ROUTED",
        on_empty=request.fallback_on_empty,on_low_confidence=request.fallback_on_low_confidence).next_channel(route,confidence)
    fallback=other is not None
    fallback_ms=0
    if fallback:
        fallback_start=time.perf_counter()
        second=search(request.model_copy(update={"retrieval_mode":other}))
        dense=dense or second["dense"];sparse=sparse or second["sparse"]
        for key,value in second.get("timingMs",{}).items():timings[key]=timings.get(key,0)+value
        fallback_ms=(time.perf_counter()-fallback_start)*1000
    search_ms=(time.perf_counter()-began)*1000;start=time.perf_counter()
    fused=fuse(dense,sparse,request.rrf_k,request.fusion_top_k) if request.execution_mode=="BASELINE" or dense and sparse else (dense or sparse)
    variants=list(dict.fromkeys(q for q in request.query_variants if q!=request.question))
    if variants:
        # Retain the original query as a full route; expand only within exactly
        # the same owner/document/version/section scope. Rerank once at the end.
        routes=[fused]
        for query in variants:
            extra=search(request.model_copy(update={"question":query,"retrieval_mode":"HYBRID","query_variants":[]}))
            routes.append(fuse(extra["dense"],extra["sparse"],request.rrf_k,request.fusion_top_k))
            for key,value in extra.get("timingMs",{}).items():timings[key]=timings.get(key,0)+value
        values={};scores={}
        for hits in routes:
            for rank,hit in enumerate(hits,1):
                key=hit['chunk_id'];values.setdefault(key,hit);scores[key]=scores.get(key,0)+1/(request.rrf_k+rank)
        fused=[values[key] for key in sorted(values,key=lambda key:(-scores[key],key))[:request.fusion_top_k]]
        search_ms=(time.perf_counter()-began)*1000
    fusion_ms=(time.perf_counter()-start)*1000;start=time.perf_counter()
    skipped=not request.rerank_enabled or not fused
    rank_result={"candidates":fused,"timingMs":{}}
    if not skipped:rank_result=rerank(RerankRequest(question=request.rerank_question or request.question,
        candidates=fused,title_boost=request.title_boost,focus_query=request.focus_query))
    ranked=rank_result["candidates"]
    rerank_ms=(time.perf_counter()-start)*1000
    response=dict(dense=dense,sparse=sparse,fused=fused,reranked=ranked,selected=ranked[:request.final_top_k],
                routing=decision|{"quality":confidence,"fallback":fallback,"fallbackReason":confidence.get('reason') if fallback else None,
                    "fallbackChannel":other,"fallbackAttempts":int(fallback),"rerankSkipped":skipped,
                    "initialRoute":route,"finalRoute":"HYBRID" if dense and sparse else "DENSE" if dense else "BM25" if sparse else route,
                    "executedChannels":(["DENSE"] if route in ("DENSE","HYBRID") else [])+(["BM25"] if route in ("BM25","HYBRID") else [])+([other] if fallback else []),
                    "requestId":request.request_id,"queryVariantCount":len(variants),"originalQueryRetained":True,"rerankCandidates":0 if skipped else len(fused)},
                timingMs=decision["timingMs"]|timings|rank_result.get("timingMs",{})|dict(search=search_ms,fallback=fallback_ms,rrf=fusion_ms,rerank=rerank_ms,total=(time.perf_counter()-began)*1000))
    try:
        import logging,json
        logging.getLogger("uvicorn.error").info("retrieval_route %s",json.dumps(dict(requestId=request.request_id,routeMode=response["routing"]["finalRoute"],
            reason=decision["reasonCode"],ruleVersion=decision["ruleVersion"],fallback=fallback,channels=len(response["routing"]["executedChannels"]),
            rerankCandidates=response["routing"]["rerankCandidates"],timingMs=response["timingMs"])))
    except Exception:pass
    return response

def _query_terms(question):
    import re
    terms=[]
    for token in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", question.lower()):
        if token.isascii():
            if len(token)>1: terms.append(token)
        else:
            terms.extend(token[i:i+2] for i in range(len(token)-1))
    return set(terms)

def truncate_for_rerank(question, text, tokenizer, max_tokens=RERANK_MAX_TOKENS):
    """Keep title and query-relevant sentences for the temporary reranker input only."""
    def token_count(value):
        return len(tokenizer(value, add_special_tokens=True, truncation=False)["input_ids"])
    if token_count(text) <= max_tokens:
        return text
    import re
    parts=[part.strip() for part in re.split(r"(?<=[。！？!?；;])\s*|\n+", text) if part.strip()]
    if not parts:
        return text
    terms=_query_terms(question)
    scored=[]
    for index, part in enumerate(parts):
        part_terms=_query_terms(part)
        score=len(terms & part_terms) + (2 if index == 0 else 0) + query_expander.sentence_bonus(question,part)
        scored.append((score, -index, index, part))
    selected=[];used=0
    for _,_,index,part in sorted(scored, reverse=True):
        count=token_count(part)
        if used + count <= max_tokens:
            selected.append((index,part));used += count
    if not selected:
        encoded=tokenizer(text, add_special_tokens=True, truncation=True, max_length=max_tokens)["input_ids"]
        return tokenizer.decode(encoded, skip_special_tokens=True)
    selected.sort()
    result="\n".join(part for _,part in selected)
    if token_count(result) > max_tokens:
        encoded=tokenizer(result, add_special_tokens=True, truncation=True, max_length=max_tokens)["input_ids"]
        result=tokenizer.decode(encoded, skip_special_tokens=True)
    return result

def rerank_scores(question,texts):
    import torch
    from transformers import AutoTokenizer,AutoModelForSequenceClassification
    metrics=_score_timing.get();started=time.perf_counter()
    if "reranker" not in models:
        models["reranker"]=(AutoTokenizer.from_pretrained(RERANKER),AutoModelForSequenceClassification.from_pretrained(RERANKER).eval())
    tokenizer,model=models["reranker"]
    if metrics is not None:metrics["rerankModelLoad"]=(time.perf_counter()-started)*1000
    started=time.perf_counter()
    texts=[truncate_for_rerank(question,text,tokenizer) for text in texts]
    if metrics is not None:metrics["rerankPreprocess"]=(time.perf_counter()-started)*1000
    scores=[]
    for start in range(0,len(texts),RERANK_BATCH_SIZE):
        started=time.perf_counter()
        inputs=tokenizer([[question,t] for t in texts[start:start+RERANK_BATCH_SIZE]],padding=True,truncation=True,max_length=RERANK_MAX_TOKENS,return_tensors="pt")
        if metrics is not None:
            metrics["rerankInputTokens"]=metrics.get("rerankInputTokens",0)+int(inputs["attention_mask"].sum().item())
            metrics["rerankPaddedTokens"]=metrics.get("rerankPaddedTokens",0)+inputs["input_ids"].numel()
        if metrics is not None:metrics["rerankTokenize"]=metrics.get("rerankTokenize",0)+(time.perf_counter()-started)*1000
        if inputs["input_ids"].is_cuda:torch.cuda.synchronize()
        started=time.perf_counter()
        with torch.inference_mode(): scores.extend(model(**inputs).logits.view(-1).float().tolist())
        if inputs["input_ids"].is_cuda:torch.cuda.synchronize()
        if metrics is not None:metrics["rerankInference"]=metrics.get("rerankInference",0)+(time.perf_counter()-started)*1000
    return scores

class RerankRequest(BaseModel):
    question: str = Field(min_length=1,max_length=2000)
    candidates: list[dict] = Field(max_length=100)
    title_boost: bool = True
    focus_query: str = Field(default="",max_length=2000)

def focus_terms(question):
    """Only query-derived attribute words; no answer values or fixed chapter aliases."""
    import re
    question=re.sub(r"^.*?专业", "", question)
    question=re.sub(r"需不需要|什么时候|不低于|不超过|告诉我|帮我|请问|是多少|是否|能否|需要|修满|至少|最低|最高|总计|总共|多少|几个|几门|几年|多久|多长|何时|哪里|的|是|请", " ",question)
    return list(dict.fromkeys(t for t in re.split(r"[\s，,。；;？?：:]+",question) if 2<=len(t)<=32))[:16]

@app.post("/rerank")
def rerank(request:RerankRequest):
    started=time.perf_counter();metrics={"rerankInference":0,"rerankTokenize":0,"rerankPreprocess":0,"rerankModelLoad":0}
    token=_score_timing.set(metrics)
    try:
        with bounded(metrics,"rerankQueue"):
            return _rerank_locked(request,metrics,started)
    finally:_score_timing.reset(token)


def _rerank_locked(request,metrics,started):
        import re
        scores=rerank_scores(request.question,[c.get("search_text") or c["content"] for c in request.candidates]) if request.candidates else []
        ranked=[]
        terms=focus_terms(request.focus_query) if request.focus_query else []
        for c,score in zip(request.candidates,scores):
            title=(c.get("sources") or [{}])[0].get("section","").split(" / ")[-1]
            title=re.sub(r"^(?:第[一二三四五六七八九十百0-9]+[章节]|[一二三四五六七八九十百0-9]+[、．.])\s*","",title)
            bonus=float(os.getenv("TITLE_MATCH_BONUS","1.0")) if request.title_boost and len(title)>=2 and title!="正文" and title in request.question else 0.0
            focus_bonus=float(os.getenv("FOCUS_TITLE_BONUS","1.0")) if terms and all(term in title for term in terms) else 0.0
            ranked.append(c|{"model_rerank_score":score,"title_bonus":bonus,"focus_bonus":focus_bonus,"rerank_score":score+bonus+focus_bonus})
        result=sorted(ranked,key=lambda c:c["rerank_score"],reverse=True)
        metrics["rerankServiceTotal"]=(time.perf_counter()-started)*1000
        return {"candidates":result,"timingMs":metrics}
