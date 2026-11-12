"""Freeze experiment inputs before inference; reuses gold without changing source mapping."""
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parents[1];base=root/"evaluation/chunk-expanded-20261019"
out=root/"evaluation/three-experiments-20261105";out.mkdir(exist_ok=True)
manifest=json.loads((base/"manifest.json").read_text(encoding="utf-8"))
previous=json.loads((root/"evaluation/retrieval-router-20261104/results.json").read_text(encoding="utf-8"))
queries={q["id"]:q for q in previous["queries"]};fixture=[]
for document in manifest["documents"]:
    chunks=json.loads((base/document["id"]/"legacy/chunks.json").read_text(encoding="utf-8"))
    order={c["chunk_id"]:i for i,c in enumerate(chunks)}
    cases=[c for c in document["cases"] if c["variant"]=="ORIGINAL"][:2]
    for case in cases:
        q=queries[case["case_id"]]
        fixture.append(dict(case=case,language=document["language"],document=document["id"],order=order,candidates=q["arms"]["BASELINE"]["fused"]))
(out/"c-fixture.json").write_text(json.dumps(fixture,ensure_ascii=False),encoding="utf-8")
protocol=dict(counts=[5,8,12,16,24],questions=len(fixture),languages={x:sum(f['language']==x for f in fixture) for x in ("zh","en")},
    candidatePool="FROZEN_RRF_TOP28_PREFIX",finalTopK=12,maxTokens=256,batchSize=8,seed=20261105,chatLlmCalls=0,
    fixtureSha256=hashlib.sha256((out/"c-fixture.json").read_bytes()).hexdigest(),dictionarySha256=hashlib.sha256((root/"config/retrieval-routing-rules.yml").read_bytes()).hexdigest())
(out/"protocol.json").write_text(json.dumps(protocol,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(protocol,ensure_ascii=False))
