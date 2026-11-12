"""Write a readable manifest for the final 30 original PDFs."""
import json,re
from pathlib import Path
import pymupdf
b=Path('evaluation/rag-quality');data=json.loads((b/'performance-corpus.json').read_text(encoding='utf-8'));rows=[]
lines=['# 性能测试 PDF 清单','', '10 个小文件、20 个大文件；中英文各 15 个。体积为实际下载字节换算的十进制 MB，未填充文件或重复拼接页面。','']
for group in ['small','large']:
 lines.extend([f'## {group}','','| 名称 | 语言 | MB | 页数 | 原始来源 |','| --- | --- | --- | --- | --- |'])
 for item in data['selected'][group]:
  with pymupdf.open(b/item['path']) as doc:
   title=doc.metadata.get('title') or ''
   if not title:
    cover=doc[0].get_text().strip().splitlines();title=next((s for s in cover if len(s)>8 and not s.isnumeric()),item['id'])
   if 'tencent.com/wp-content' in item['url']:
    year=re.search(r'(20\d\d)-ESG',item['url']);title='Tencent ESG '+(year.group(1) if year else '')
  rows.append(dict(**item,title=title))
  lines.append(f"| {title.replace('|','/')} | {item['language']} | {item['bytes']/1e6:.2f} | {item['pages']} | [PDF]({item['url']}) |")
lines+=['','大文件来源集中于华为不同年份和业务的报告；这批用于性能，不作为跨机构质量盲测集。完整 SHA256、文本字符数与缓存路径见 performance-inventory.json。']
(b/'performance-inventory.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8');(b/'performance-corpus.md').write_text('\n'.join(lines)+'\n',encoding='utf-8');print('Final 30-document manifest written')
