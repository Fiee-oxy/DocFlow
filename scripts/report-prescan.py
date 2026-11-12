"""Page-scoped frozen gold checks and exact baseline/new text comparison."""
import hashlib
import json
import re
import statistics
import unicodedata
from pathlib import Path


def norm(text):return re.sub(r"\s+","",unicodedata.normalize("NFKC",text).casefold())


def main():
    root=Path(__file__).resolve().parents[1];folder=root/"evaluation/prescan-20261102"
    result=json.loads((folder/"results.json").read_text(encoding="utf-8"))
    manifest=json.loads((root/"evaluation/parser-ab-20261018/inputs/manifest.json").read_text(encoding="utf-8"))
    documents={d["id"]+".pdf":d for d in manifest["documents"]}
    hashes={};scores=[]
    for trial in result["parses"]:
        events=json.loads((folder/trial["eventsFile"]).read_text(encoding="utf-8"))
        blocks=[b for e in events if e["type"]=="batch" for b in e["blocks"]]
        content=[{k:b.get(k) for k in ("text","pages","section","label")} for b in blocks]
        sha=hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        hashes[(trial["document"],trial["arm"])]=sha
        cases=[]
        for case in documents[trial["document"]]["cases"]:
            units=[]
            for unit in case["units"]:
                text=norm("\n".join(b["text"] for b in blocks if set(b.get("pages",[])) & set(unit["pages"])))
                found=[a for a in unit["atoms"] if norm(a) in text]
                units.append(dict(pages=unit["pages"],atoms=unit["atoms"],found=found,complete=len(found)==len(unit["atoms"])))
            cases.append(dict(id=case["case_id"],complete=all(u["complete"] for u in units),units=units))
        scores.append(trial | dict(contentHash=sha,goldCases=cases,completeGoldCases=sum(c["complete"] for c in cases),
                                  routes=[e.get("parser") for e in events if e["type"]=="batch"]))
    comparisons=[dict(document=name,publishedContentIdentical=hashes.get((name,"OLD_AUTO"))==hashes.get((name,"PRESCAN_AUTO")),
                      bothCompleted=all(p["complete"] for p in result["parses"] if p["document"]==name and p["arm"] in ("OLD_AUTO","PRESCAN_AUTO")))
                 for name in sorted({p["document"] for p in result["parses"]})]
    profiles=[dict(document=p["document"],kind=p["kind"],medianMs=round(statistics.median(p["seconds"])*1000,3),
                   totalPages=p["profile"].get("totalPages"),scannedPages=len(p["profile"].get("pages",[])),
                   coverage=p["profile"].get("coverage","FULL"),bytes=p["profile"].get("bytes")) for p in result["profiles"]]
    (folder/"scored.json").write_text(json.dumps(dict(profiles=profiles,parses=scores,comparisons=comparisons,
        goldStatus="SOURCE_ALIGNED_PENDING_HUMAN_REVIEW",metric="PAGE_SCOPED_LITERAL_ATOMS_NOT_RETRIEVAL_ACCURACY"),ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(dict(profiles=profiles,parses=[{k:p[k] for k in ("document","arm","seconds","firstBatchSeconds","complete","completeGoldCases")} for p in scores],comparisons=comparisons),ensure_ascii=False,indent=2))


if __name__=="__main__":main()
