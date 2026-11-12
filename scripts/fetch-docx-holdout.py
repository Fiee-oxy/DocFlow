"""Fetch public, previously unused DOCX files; retain provenance and failed attempts."""
import hashlib
import json
from pathlib import Path
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation/docx-holdout-20261027"
SOURCES = [
    {"key":"zh_shiep_quality", "language":"zh", "kind":"REAL",
     "title":"上海电力大学2023-2024学年本科教学质量报告",
     "source_page":"https://jwc.shiep.edu.cn/_t196/f3/d8/c227a259032/page.htm",
     "download_url":"https://jwc.shiep.edu.cn/_upload/article/files/2c/ad/4ff7dcae40229f45d3822af26009/5aef581f-50e3-4985-bc13-b10694012041.docx"},
    {"key":"en_cargo_bikes", "language":"en", "kind":"REAL",
     "title":"Oxfordshire Cargo Bikes Guidance",
     "source_page":"https://www.oxfordshire.gov.uk/transport-and-travel/local-transport-and-connectivity-plan/freight-and-logistics",
     "download_url":"https://www.oxfordshire.gov.uk/sites/default/files/file/roads-and-transport-policies-and-plans/CargoBikesGuidance.docx"},
    {"key":"zh_maoming_quality", "language":"zh", "kind":"REAL",
     "title":"广东茂名农林科技职业学院高等职业教育质量报告（2024年度）",
     "source_page":"https://www.gdafc.edu.cn/info/1010/4137.htm",
     "download_url":"https://www.gdafc.edu.cn/system/_content/download.jsp?urltype=news.DownloadAttachUrl&owner=1798983074&wbfileid=11302008"},
    {"key":"zh_tibet_quality", "language":"zh", "kind":"REAL",
     "title":"西藏职业技术学院中国职业教育质量年度报告（2024年）",
     "source_page":"https://www.xzgzy.cn/info/1022/3507.htm",
     "download_url":"https://www.xzgzy.cn/system/_content/download.jsp?urltype=news.DownloadAttachUrl&owner=1790166901&wbfileid=11749778"},
]


def fetch(item):
    output = OUT / "inputs" / (item["key"] + ".docx")
    temporary = output.with_suffix(".download")
    result = dict(item)
    started = time.perf_counter()
    try:
        if not output.exists():
            with requests.get(item["download_url"],stream=True,timeout=(12,40)) as response:
                response.raise_for_status()
                size=0
                with temporary.open("wb") as target:
                    for part in response.iter_content(1024*1024):
                        size+=len(part)
                        if size>100*1024*1024 or time.perf_counter()-started>240:
                            raise ValueError("Public download exceeded bounded size/time")
                        target.write(part)
            if not zipfile.is_zipfile(temporary):
                raise ValueError("Attachment is not a ZIP/DOCX; no HTML challenge is accepted")
            with zipfile.ZipFile(temporary) as package:
                package.getinfo("word/document.xml")
            temporary.replace(output)
        with zipfile.ZipFile(output) as package:
            result.update(bytes=output.stat().st_size,document_xml_bytes=package.getinfo("word/document.xml").file_size,
                          largest_parts=[{"part":p.filename,"bytes":p.file_size} for p in sorted(package.infolist(),key=lambda p:p.file_size,reverse=True)[:6]])
        result.update(status="DOWNLOADED",sha256=hashlib.file_digest(output.open("rb"),"sha256").hexdigest(),
                      path=str(output.relative_to(ROOT)).replace("\\","/"))
    except Exception as error:
        result.update(status="DOWNLOAD_FAILED",error=type(error).__name__+": "+str(error))
        if temporary.exists():
            temporary.unlink()
    result["download_ms"]=round((time.perf_counter()-started)*1000,3)
    return result


def main():
    (OUT/"inputs").mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows=list(pool.map(fetch,SOURCES))
    old=json.loads((ROOT/"evaluation/docx-stream-ab-20261027/manifest.json").read_text(encoding="utf-8"))
    previous={d.get("sha256") for d in old["documents"]}
    for row in rows:
        if row.get("sha256") in previous:
            raise ValueError("Holdout source duplicates development document")
    (OUT/"download-attempts.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
    good=[r for r in rows if r["status"]=="DOWNLOADED"]
    # Keep candidate identities fixed even if a parser later rejects one.
    (OUT/"manifest.json").write_text(json.dumps({"documents":good,"llmCalls":0,"defaultChanged":False,
        "selection":"Public documents selected before any parser/retrieval results; download failures recorded separately"},ensure_ascii=False,indent=2),encoding="utf-8")
    for row in rows:
        print(json.dumps({k:row.get(k) for k in ["key","status","bytes","document_xml_bytes","error"]},ensure_ascii=True),flush=True)


if __name__=="__main__":
    main()
