"""Reuse the disposable immutable corpus after final code hardening; no reindex."""
import json,os,sys,hashlib
from pathlib import Path
sys.path.insert(0,"/retrieval")
import app as r
if __name__=="__main__":
    root=Path("/tmp/http-eval");env=json.loads((root/"service-environment.json").read_text())
    if "routing-v2-milvus" not in os.environ.get("MILVUS_URI","") or not env["collection"].startswith("routing_v2_"):raise RuntimeError("Disposable corpus required")
    r.COLLECTION=env["collection"];r.encode(["warmup"]);r.rerank_scores("warmup",["warmup evidence"])
    env["finalServiceHashes"]={p:hashlib.sha256((Path("/retrieval")/p).read_bytes()).hexdigest() for p in ("app.py","retrieval_router.py")}
    (root/"service-environment-final.json").write_text(json.dumps(env,indent=2),encoding="utf-8")
    import uvicorn
    uvicorn.run(r.app,host="0.0.0.0",port=5002,workers=1,limit_concurrency=8)
