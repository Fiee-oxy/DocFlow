"""Literal, page-scoped gold-atom preservation; not answer accuracy."""
import json
import re
import unicodedata
from pathlib import Path


def normalize(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())


def main():
    root=Path(__file__).resolve().parents[1]
    folder=root/"evaluation/adaptive-parser-20261030"
    manifest=json.loads((root/"evaluation/parser-ab-20261018/inputs/manifest.json").read_text(encoding="utf-8"))
    document=next(d for d in manifest["documents"] if d["id"]=="perf-5a023e1dce750ab2")
    results=[]
    for mode in ("FAST","DOCLING","AUTO"):
        events=json.loads((folder/f"benchmark-{mode}-events.json").read_text(encoding="utf-8"))
        blocks=[b for e in events if e["type"]=="batch" for b in e["blocks"]]
        cases=[]
        for case in document["cases"]:
            checks=[]
            for unit in case["units"]:
                text=normalize("\n".join(b["text"] for b in blocks if set(b.get("pages",[])) & set(unit["pages"])))
                checks.append(dict(pages=unit["pages"],atoms=unit["atoms"],found=[a for a in unit["atoms"] if normalize(a) in text],
                                   complete=all(normalize(a) in text for a in unit["atoms"])))
            cases.append(dict(caseId=case["case_id"],question=case["question"],units=checks,complete=all(c["complete"] for c in checks)))
        results.append(dict(mode=mode,completeCases=sum(c["complete"] for c in cases),cases=cases))
    (folder/"source-preservation.json").write_text(json.dumps(dict(metric="PAGE_SCOPED_LITERAL_GOLD_ATOMS_NOT_RETRIEVAL",goldStatus=document["cases"][0].get("label_status"),results=results),ensure_ascii=False,indent=2),encoding="utf-8")
    print([(r["mode"],r["completeCases"],len(r["cases"])) for r in results])


if __name__=="__main__":main()
