"""Keep ten small PDFs while including scientific, manual and financial formats."""
import importlib.util,json
from pathlib import Path
spec=importlib.util.spec_from_file_location('prepare',Path(__file__).with_name('prepare-rag-performance-corpus.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
b=m.BASE;catalog=json.loads((b/'sources.json').read_text(encoding='utf-8'));corpus=json.loads((b/'performance-corpus.json').read_text(encoding='utf-8'))
ids=['financebench-3m-2018','qasper-1912.01214','qasper-1810.08699','cn-xiaomi-mi2-manual','cn-maotai-2024']
new=[]
for key in ids:
 item=next(x for x in catalog['files'] if x['id']==key)
 result=m.candidate((item['language'],item['url'],item.get('source_page',item['url'])))
 assert result['status']=='VERIFIED' and result['group']=='small'
 new.append(result)
for lang,n in [('zh',3),('en',2)]:new.extend([r for r in corpus['selected']['small'] if r['language']==lang][:n])
assert len(new)==10 and len({r['sha256'] for r in new})==10
corpus['selected']['small']=new;corpus['selection_note']='Small set diversified across papers, manual, financial and ESG reports. Large set is publisher-concentrated; performance only, no generalization claim. Earlier uploaded samples remain separate warmup/exception records.'
(b/'performance-corpus.json').write_text(json.dumps(corpus,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'small':len(new),'large':len(corpus['selected']['large']),'languages':{l:sum(r['language']==l for r in new) for l in ['zh','en']}}))
