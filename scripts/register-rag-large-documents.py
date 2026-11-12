"""Register and inspect authentic large PDF evaluation sources (no LLM)."""
import json, hashlib
from pathlib import Path
import pymupdf

BASE=Path('evaluation/rag-quality')
ITEMS=[
    dict(id='en-huawei-2024',kind='pdf',language='en',path='documents/en_huawei_2024.pdf',url='https://www.huawei.com/minisite/annual-report-download/annual_report_2024_en.pdf',source_page='https://www.huawei.com/en/annual-report/2024/',split='development',document_family='huawei-2024',annotation_status='UNANNOTATED_NOT_SCORED'),
    dict(id='en-polypeptide-2024',kind='pdf',language='en',path='documents/en_polypeptide_2024.pdf',url='https://report.polypeptide.com/ar/24/app/uploads/PPG_Annual_Report_2024.pdf',source_page='https://report.polypeptide.com/ar/24/download-center-archive',split='holdout_candidate',document_family='polypeptide-2024',annotation_status='UNANNOTATED_NOT_SCORED'),
    dict(id='cn-huawei-2024',kind='pdf',language='zh',path='documents/cn_huawei_2024.pdf',url='https://www.huawei.com/minisite/annual-report-download/annual_report_2024_cn.pdf',source_page='https://www.huawei.com/cn/annual-report/',split='development',document_family='huawei-2024',annotation_status='UNANNOTATED_NOT_SCORED')]

def main():
    catalog=json.loads((BASE/'sources.json').read_text(encoding='utf-8'))
    existing={x['id'] for x in catalog['files']}
    catalog['files'].extend(x for x in ITEMS if x['id'] not in existing)
    catalog['evaluation_policy']['large_document_minimum']={'zh':1,'en':1,'minimum_actual_bytes':18000000,'note':'Use original PDFs, never padding or repeating pages. Same document family stays in one split.'}
    (BASE/'sources.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf-8')
    reports=[]
    for item in ITEMS:
        path=BASE/item['path']
        temporary=path.with_suffix(path.suffix+'.part')
        if temporary.exists():
            try:
                with pymupdf.open(temporary) as candidate:
                    valid=len(candidate)>0 and not candidate.is_repaired
                if valid:temporary.replace(path)
            except Exception:pass
        if not path.exists():continue
        with pymupdf.open(path) as doc:
            if not len(doc) or doc.is_repaired:continue
            lengths=[len(p.get_text()) for p in doc]
            reports.append(dict(**item,bytes=path.stat().st_size,decimal_MB=round(path.stat().st_size/1e6,2),MiB=round(path.stat().st_size/1048576,2),pages=len(doc),text_characters=sum(lengths),pages_with_at_least_100_text_characters=sum(n>=100 for n in lengths),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    (BASE/'large-document-inventory.json').write_text(json.dumps({'llm_calls':0,'documents':reports,'status':'DOWNLOADED_INSPECTED_NOT_YET_INGESTED_OR_SCORED'},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(reports,ensure_ascii=True,indent=2))
if __name__=='__main__':main()
