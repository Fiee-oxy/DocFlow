"""Download new source families, audit duplicates, extract reference pages only."""
import concurrent.futures,hashlib,json,time,urllib.request
from pathlib import Path
import fitz
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evaluation/fresh-holdout-20261112'
SOURCES=[
 ('zh-ai-governance-v1','zh','人工智能安全治理框架 V1.0','https://www.cac.gov.cn/rootimages/uploadimg/1727568303900999/1727568303900999.pdf','https://www.cac.gov.cn/2024-09/09/c_1727567886199789.htm'),
 ('zh-data-security-law-2021','zh','中华人民共和国数据安全法（2021年文本）','https://zjca.miit.gov.cn/cms_files/filemanager/1528008671/attach/20233/513732cc7dfb4dbbad87fdaf0f997151.pdf','https://www.cac.gov.cn/2021-06/11/c_1624994566919140.htm'),
 ('zh-ecology-2024','zh','2024中国生态环境状况公报','https://www.mee.gov.cn/hjzl/sthjzk/zghjzkgb/202506/P020250604527010717462.pdf','https://www.mee.gov.cn/hjzl/sthjzk/zghjzkgb/'),
 ('zh-catl-esg-2024','zh','宁德时代2024年度环境、社会与公司治理报告','https://www.catl.com/uploads/1/file/public/202503/20250328110514_yhfqo19im4.pdf','https://www.catl.com/about/sustainability/'),
 ('en-nist-ai-rmf-1','en','NIST Artificial Intelligence Risk Management Framework 1.0','https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf','https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10'),
 ('en-rfc8446','en','RFC 8446: TLS 1.3','https://www.rfc-editor.org/rfc/pdfrfc/rfc8446.txt.pdf','https://www.rfc-editor.org/info/rfc8446'),
 ('en-artemis-plan-2020','en','NASA Artemis Plan (September 2020)','https://www.nasa.gov/wp-content/uploads/2020/12/artemis_plan-20200921.pdf','https://www.nasa.gov/news-release/nasa-publishes-artemis-plan-to-land-first-woman-next-man-on-moon-in-2024/'),
 ('en-euratom-2024','en','Euratom Supply Agency Annual Report 2024','https://euratom-supply.ec.europa.eu/document/download/4991f977-5fa7-415e-8b7f-04714f01c533_en?filename=202509773_PDFA2A_MJ0125120ENA_002.pdf','https://euratom-supply.ec.europa.eu/publications/esa-annual-reports_en'),
]
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def save(name,data):(OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def fetch(source):
    key,lang,title,url,page=source;path=OUT/'documents'/(key+'.pdf');started=time.perf_counter()
    try:
        if not path.exists():
            request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (document evaluation)'})
            with urllib.request.urlopen(request,timeout=90) as response:path.write_bytes(response.read(150_000_001))
        assert path.read_bytes()[:5]==b'%PDF-','Response is not PDF'
        assert path.stat().st_size<=150_000_000,'File exceeds bound'
        pdf=fitz.open(path);pages=[]
        for i,p in enumerate(pdf):pages.append(dict(physical_page=i+1,text=p.get_text(sort=True)))
        (OUT/'references'/(key+'.json')).write_text(json.dumps(pages,ensure_ascii=False),encoding='utf-8')
        # Human-readable local annotation aid, never used as a pipeline parse result.
        (OUT/'references'/(key+'.txt')).write_text('\n\n'.join(f"=== PHYSICAL PAGE {p['physical_page']} ===\n{p['text']}" for p in pages),encoding='utf-8')
        return dict(id=key,language=lang,title=title,url=url,source_page=page,path=str(path.relative_to(ROOT)).replace('\\','/'),
            bytes=path.stat().st_size,size_mb=round(path.stat().st_size/1_000_000,3),pages=len(pdf),sha256=sha(path),
            size_group='large' if path.stat().st_size>=10_000_000 else 'small',text_pages=sum(bool(p['text'].strip()) for p in pages),
            role='UNSCORED_DOCUMENT_HOLDOUT',status='DOWNLOADED',seconds=round(time.perf_counter()-started,2))
    except Exception as e:return dict(id=key,language=lang,url=url,status='FAILED',error=type(e).__name__+': '+str(e))
def main():
    for d in ('documents','references','qa'): (OUT/d).mkdir(parents=True,exist_ok=True)
    prior={}
    for path in (ROOT/'evaluation').rglob('*.pdf'):
        if OUT in path.parents:continue
        prior.setdefault(sha(path),[]).append(str(path.relative_to(ROOT)).replace('\\','/'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        rows=list(executor.map(fetch,SOURCES))
    for row in rows:
        if row['status']=='DOWNLOADED':
            row['prior_pdf_matches']=prior.get(row['sha256'],[])
            row['new_file_fingerprint']=not row['prior_pdf_matches']
    save('manifest.json',dict(created='2026-11-12',source_families='CAC, NPC/MIIT, MEE, CATL, NIST, IETF, NASA, Euratom Supply Agency',
        prior_unique_pdf_fingerprints=len(prior),documents=rows,llm_calls=0,production_mutations=0,
        reference_extraction='PyMuPDF 1.26.6 sorted page text; NOT production parser output'))
    print(json.dumps(rows,ensure_ascii=True))
if __name__=='__main__':main()
