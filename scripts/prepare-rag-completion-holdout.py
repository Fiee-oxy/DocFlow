"""Freeze new-document questions from original PDFs, before any retrieval of these documents."""
import hashlib,json,sys
from pathlib import Path
import pymupdf
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'evaluation/rag-quality';RUN=BASE/'outputs/completion-holdout-20261014'
definitions={
 'pbc-2024q4':('zh','2024年第四季度中国货币政策执行报告','https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/5587716/2025022618190099812.pdf',[
  ('2024年全年降准几次，合计多少个百分点，提供多少长期流动性？',[2],['两次降准','1个百分点','超过2万亿元']),
  ('2024年末社会融资规模存量和M2同比增速分别是多少？',[3],['8.0%','7.3%']),
  ('2024年12月新发放企业贷款和个人住房贷款加权平均利率分别是多少？',[10],['企业贷款加权平均利率','3.34','个人住房贷款加权平均利率','3.09']),
  ('2024年同业拆借累计成交额和日均成交额分别是多少？',[33],['103.1万亿元','4109亿元']),
  ('报告中存款保险的偿付限额是多少，能为多大比例的存款人提供全额保障？',[32],['50万元','99%以上']),
  ('2024年末隔夜和1周Shibor分别是多少？',[33],['1.45%','1.97%'])]),
 'rfc9110':('en','RFC 9110 HTTP Semantics','https://www.rfc-editor.org/rfc/rfc9110.pdf',[
  ('Which HTTP methods are defined as safe in RFC 9110?',[73],['GET','HEAD','OPTIONS','TRACE']),
  ('What makes an HTTP method idempotent, and which methods have this property?',[73],['intended effect','multiple identical requests','PUT','DELETE','safe request methods']),
  ('What two forms can the Retry-After field value take?',[89],['HTTP-date','number of seconds']),
  ('What status code may a server return for an unsupported Expect value?',[82],['100-continue','417','Expectation Failed']),
  ('What does HTTP 405 mean and which response header must the origin server generate?',[138],['not supported by the target resource','Allow header field']),
  ('For HTTP 413, what should the server send when the condition is temporary?',[140],['temporary','Retry-After header field'])])
}
def norm(text):return ''.join(text.lower().split())
def main():
 RUN.mkdir(parents=True,exist_ok=True)
 if (RUN/'freeze.json').exists():raise SystemExit('Holdout already frozen; refusing to rewrite labels')
 cases=[];items=[]
 for key,(lang,title,url,questions) in definitions.items():
  path=BASE/'holdout-documents'/(key+'.pdf');document=pymupdf.open(path)
  pages=[page.get_text() for page in document];sha=hashlib.sha256(path.read_bytes()).hexdigest()
  items.append({'id':key,'path':str(path.relative_to(BASE)),'language':lang,'title':title,'source_url':url,'sha256':sha,'bytes':path.stat().st_size,'pages':len(document)})
  for i,(q,physical,atoms) in enumerate(questions,1):
   reference='\n'.join(pages[p-1] for p in physical)
   assert all(norm(atom) in norm(reference) for atom in atoms),(key,i,'gold absent from original PDF')
   cases.append({'case_id':f'{key}-q{i}','document_key':key,'language':lang,'title':title,'question':q,'group':'DOCUMENT_HOLDOUT','kind':'natural','source_sha256':sha,
                 'units':[{'pages':physical,'atoms':atoms}],'reference_text':reference})
 data=json.dumps(cases,ensure_ascii=False,indent=2).encode();(RUN/'cases.json').write_bytes(data)
 (RUN/'freeze.json').write_text(json.dumps({'sha256':hashlib.sha256(data).hexdigest(),'policy':'New whole documents; no retrieval before freezing; no tuning on this set; assistant-authored labels, independent human review pending.'},indent=2),encoding='utf-8')
 (RUN/'corpus.json').write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'documents':len(items),'questions':len(cases),'source_validation':'PASS'}))
if __name__=='__main__':main()
