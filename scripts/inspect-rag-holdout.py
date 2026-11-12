"""Read-only source inspection, before freezing holdout labels."""
import json,re
from pathlib import Path
import pymupdf
root=Path(__file__).resolve().parents[1]
for name,terms in [('pbc-2024q4.pdf',['两次降准','社会融资规模存量','科技创新和技术改造','同业拆借','存款准备金率','贷款加权平均利率'])]:
    document=pymupdf.open(root/'evaluation/rag-quality/holdout-documents'/name)
    pages=[' '.join(page.get_text().split()) for page in document]
    for term in terms:
        found=[]
        for i,text in enumerate(pages):
            if term in text:
                pos=text.index(term);found.append({'page':i+1,'excerpt':text[max(0,pos-80):pos+800]})
        print(json.dumps({'term':term,'matches':found[:3]},ensure_ascii=True))
