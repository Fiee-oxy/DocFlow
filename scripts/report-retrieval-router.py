"""Summarize paired latency and evidence coverage, without tuning rules on results."""
import json
import math
import statistics
from pathlib import Path


def describe(values):
    values=sorted(values)
    return dict(p50=statistics.median(values),p95=values[max(0,math.ceil(len(values)*.95)-1)],mean=statistics.mean(values))


def main():
    folder=Path(__file__).resolve().parents[1]/"evaluation/retrieval-router-20261104"
    data=json.loads((folder/"results.json").read_text(encoding="utf-8"));queries=data["queries"]
    summary=dict(count=len(queries),groups={},regressions=[],improvements=[],routes={},fallbacks=0,rerankSkipped=0,
                 parameterChangesAfterEvaluation=False,chatLlmCalls=0)
    for name,group in [("all",queries)]+[(language,[q for q in queries if q["language"]==language]) for language in ("zh","en")]+[(variant,[q for q in queries if q["variant"]==variant]) for variant in ("ORIGINAL","PARAPHRASE")]:
        if not group:continue
        result={}
        for arm in ("BASELINE","ROUTED"):
            result[arm]=dict(totalMs=describe([q["arms"][arm]["observedTotalMs"] for q in group]),
                stagesMs={stage:describe([q["arms"][arm]["timingMs"].get(stage,0) for q in group]) for stage in ("dense","bm25","search","rrf","rerank")},
                coverage={stage:sum(q["arms"][arm]["coverage"][stage]["complete"] for q in group) for stage in ("union","fused","top12")},
                rerankPairs=sum(0 if q["arms"][arm].get("routing",{}).get("rerankSkipped") else len(q["arms"][arm]["fused"]) for q in group))
        result["observedReductionPercent"]={stat:(1-result["ROUTED"]["totalMs"][stat]/result["BASELINE"]["totalMs"][stat])*100 for stat in ("p50","p95","mean")}
        summary["groups"][name]=result
    for q in queries:
        decision=q["arms"]["ROUTED"]["routing"];route=decision["finalRoute"]
        summary["routes"][route]=summary["routes"].get(route,0)+1
        summary["fallbacks"]+=int(decision["fallback"]);summary["rerankSkipped"]+=int(decision["rerankSkipped"])
        a=q["arms"]["BASELINE"]["coverage"]["top12"]["complete"];b=q["arms"]["ROUTED"]["coverage"]["top12"]["complete"]
        if a and not b:summary["regressions"].append(q["id"])
        if b and not a:summary["improvements"].append(q["id"])
    summary["collectionsRemoved"]=all(d["cleanup"] for d in data["documents"])
    (folder/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
