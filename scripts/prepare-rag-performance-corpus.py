"""Discover genuine PDFs from publisher pages, verify size/hash/text, select 10+20."""
import concurrent.futures, hashlib, json, subprocess, re, time
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote
from html.parser import HTMLParser
import pymupdf
BASE=Path('evaluation/rag-quality'); DEST=BASE/'performance-documents'; DEST.mkdir(exist_ok=True)
PAGES=[
 ('zh','https://www.huawei.com/cn/sustainability/sustainability-report/'),
 ('en','https://www.huawei.com/en/sustainability/sustainability-report/'),
 ('zh','https://www.tencent.com/zh-cn/esg-report-type/ssv-zh-cn/'),
 ('zh','https://www.tencent.com/zh-cn/esg-report-type/esg-zh-cn/'),
 ('en','https://www.tencent.com/esg-report-type/esg/'),
 ('zh','https://www.jarlly.com/esg/download'),
 ('zh','https://esg.merry.com.tw/document/'),
 ('zh','https://sustainable.thu.edu.tw/download/'),
 ('zh','https://www.stspesg.com/'),
 ('zh','https://www.o-bank.com/zh-TW/about/csr/csr-report_feedback'),
 ('zh','https://www.cht.com.tw/zh-tw/home/cht/esg/esg-report/tnfd-report'),
 ('zh','https://www.huawei.com/cn/annual-report/'),
 ('en','https://www.huawei.com/en/annual-report/'),
 ('zh','https://www.basf.com/cn/zh/media/publications'),
 ('en','https://www.wii.gov.in/annual-report'),
 ('en','https://www.niesbud.nic.in/Annual-Reports.html'),
 ('en','https://www.rnfiservices.com/investor-information.php'),
 ('en','https://www.essexjunction.gov/departments/finance/annual-reports'),
 ('en','https://www.aa.co.nz/about/newsroom/aa-publications/'),
 ('en','https://www.ikea.com/us/en/newsroom/media-resources/documents-and-reports-pub3b485f10/'),
 *[('en',f'https://annualreport{y}.volkswagen-group.com/services/downloads.html') for y in [2023,2024,2025]],
]
class Links(HTMLParser):
 def __init__(self):super().__init__();self.links=[]
 def handle_starttag(self,tag,attrs):
  for k,v in attrs:
   if k=='href' and v and '.pdf' in v.lower():self.links.append(v)
def fetch(url,path,seconds=180,cap=85000000):
 r=subprocess.run(['curl.exe','--fail','--location','--silent','--show-error','--max-time',str(seconds),'--max-filesize',str(cap),'--output',str(path),quote(url,safe=':/?=&%+#')],capture_output=True)
 return r.returncode==0
def candidate(item):
 lang,url,page=item; key=hashlib.sha256(url.encode()).hexdigest()[:16];path=DEST/(key+'.pdf');part=path.with_suffix('.part')
 started=time.monotonic()
 try:
  if not path.exists():
   if not fetch(url,part):return {'url':url,'status':'DOWNLOAD_FAILED'}
   with pymupdf.open(part) as d:
    if not len(d) or d.is_repaired:raise ValueError('Incomplete PDF')
   part.replace(path)
  with pymupdf.open(path) as d:
   lengths=[len(p.get_text()) for p in d]
   sample=''.join(d[i].get_text() for i in range(min(10,len(d))))
   cjk=sum('\u4e00'<=c<='\u9fff' for c in sample)
   if cjk>100:lang='zh'
   elif sum(c.isascii() and c.isalpha() for c in sample)>100:lang='en'
   chars=sum(lengths)
  size=path.stat().st_size
  return {'id':'perf-'+key,'url':url,'source_page':page,'language':lang,'path':str(path.relative_to(BASE)).replace('\\','/'),'bytes':size,'pages':len(lengths),'text_characters':chars,'text_pages':sum(n>=100 for n in lengths),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'group':'large' if size>=18000000 else 'small' if size<=5000000 else 'medium','status':'VERIFIED','download_inspect_seconds':round(time.monotonic()-started,2)}
 except Exception as e:return {'url':url,'status':'INVALID','error':str(e)}
 finally:
  if part.exists():part.unlink()
def main():
 candidates=[]
 def discover(pair):
  lang,page=pair
  html=DEST/(hashlib.sha256(page.encode()).hexdigest()[:16]+'.html')
  if html.exists() or fetch(page,html,20,10000000):
   parser=Links();parser.feed(html.read_text(encoding='utf-8',errors='replace'))
   return [(lang,urljoin(page,link),page) for link in parser.links]
  return []
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as discovery:
  for found in discovery.map(discover,PAGES):candidates.extend(found)
 # Existing authentic originals are copied by URL download cache mapping below.
 catalog=json.loads((BASE/'sources.json').read_text(encoding='utf-8'))
 for item in catalog['files']:
  if item.get('kind')=='pdf':candidates.append((item.get('language','en'),item['url'],item.get('source_page',item['url'])))
 unique={url:(lang,url,page) for lang,url,page in candidates}
 # Full annual reports before section PDFs; avoid evaluating future-dated reports.
 ordered=sorted(unique.values(),key=lambda x:('2026' in x[1],not ('huawei' in x[1] or 'tencent' in x[1]),x[0]!='zh',not any(t in x[1].lower() for t in ['annual-report-vw','annual-report-202','annual_report_202','full','esg','sustainability']),x[1]))
 ordered=[x for x in ordered if not re.search(r'/(add-|cfs-|div-|cg-|gmr-|notes-|sr-)',x[1])]
 (BASE/'performance-candidates.json').write_text(json.dumps(ordered,indent=2),encoding='utf-8')
 results=[];seen=set();selected={'small':[],'large':[]}
 # Seed originals already validated locally, avoiding unnecessary network traffic.
 for item in catalog['files']:
  src=BASE/item.get('path','')
  if item.get('kind')=='pdf' and src.is_file():
   target=DEST/(hashlib.sha256(item['url'].encode()).hexdigest()[:16]+'.pdf')
   if not target.exists():
    import shutil;shutil.copyfile(src,target)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  for start in range(0,len(ordered),4):
   for result in pool.map(candidate,ordered[start:start+4]):
    results.append(result)
    if result['status']=='VERIFIED' and result['sha256'] not in seen:
     seen.add(result['sha256']);g=result['group']
     quota={'small':5,'large':10}
     if g in selected and result['text_characters']>100 and sum(x['language']==result['language'] for x in selected[g])<quota[g]:selected[g].append(result)
    (BASE/'performance-corpus.json').write_text(json.dumps({'target':{'small':10,'large':20},'selected':selected,'all_results':results,'llm_calls':0},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'event':'corpus_progress','small':len(selected['small']),'large':len(selected['large']),'last_status':result['status'],'bytes':result.get('bytes'),'url':result['url']}),flush=True)
   if len(selected['small'])==10 and len(selected['large'])==20:break
 print(json.dumps({'event':'corpus_finished','small':len(selected['small']),'large':len(selected['large'])}),flush=True)
if __name__=='__main__':main()
