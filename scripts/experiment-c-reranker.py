"""Real per-N inference on identical frozen RRF prefixes. No retrieval or chat LLM."""
import hashlib,json,os,random,sys,time
from pathlib import Path
sys.path.insert(0,"/retrieval");sys.path.insert(0,"/work/scripts")
import app as r
from evidence_metrics import coverage

if __name__=="__main__":
    import torch
    torch.set_num_threads(2)
    out=Path("/work/evaluation/three-experiments-20261105");raw=(out/"c-fixture.json").read_bytes();fixture=json.loads(raw)
    r.rerank(r.RerankRequest(question=fixture[0]["case"]["question"],candidates=fixture[0]["candidates"][:24]))
    report=dict(scope="REAL_RERANK_ONLY_FIXED_RRF_PREFIX",fixtureSha256=hashlib.sha256(raw).hexdigest(),threads=torch.get_num_threads(),tokens=r.RERANK_MAX_TOKENS,batch=r.RERANK_BATCH_SIZE,records=[])
    for index,case in enumerate(fixture):
        counts=[5,8,12,16,24];random.Random(20261105+index).shuffle(counts)
        for n in counts:
            inputs=case["candidates"][:n];start=time.perf_counter()
            result=r.rerank(r.RerankRequest(question=case["case"]["question"],candidates=inputs));elapsed=(time.perf_counter()-start)*1000
            def score(hits):return coverage([s for h in hits for s in h.get("sources",[])],case["case"],case["order"])
            record=dict(id=case["case"]["case_id"],language=case["language"],count=n,clientFunctionMs=elapsed,timingMs=result["timingMs"],
                inputComplete=score(inputs),outputComplete=score(result["candidates"][:min(12,n)]),rankedIds=[h["chunk_id"] for h in result["candidates"]],
                inputIds=[h["chunk_id"] for h in inputs])
            report["records"].append(record);(out/"C-rerank-only.json").write_text(json.dumps(report,ensure_ascii=False),encoding="utf-8")
            print(json.dumps(dict(case=record['id'],count=n,inferenceMs=round(result['timingMs']['rerankInference'],2),complete=record['outputComplete']['complete'])),flush=True)
