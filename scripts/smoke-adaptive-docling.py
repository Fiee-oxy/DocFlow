"""Real HTTP/model smoke tests against the existing parser service. Synthetic only."""
import json,time
from pathlib import Path
import fitz,requests

def fixture(kind):
    d=fitz.open();p=d.new_page()
    if kind=='scan':
        raster=fitz.open();rp=raster.new_page(width=600,height=300);rp.insert_text((40,80),'OCR evidence: graduation requires 160 credits.',fontsize=18)
        image=rp.get_pixmap(matrix=fitz.Matrix(2,2));p.insert_image(p.rect,pixmap=image);raster.close()
    else:
        p.insert_text((40,60),'Revenue evidence 123 in 2024.',fontsize=16)
        if kind=='table':
            for y in [100,140,180]:p.draw_line((40,y),(400,y))
            for x in [40,200,400]:p.draw_line((x,100),(x,180))
            p.insert_text((50,125),'Name');p.insert_text((210,125),'Amount');p.insert_text((50,165),'Revenue');p.insert_text((210,165),'123')
    data=d.tobytes();d.close();return data

if __name__=='__main__':
    for attempt in range(30):
        try:
            ready=requests.get('http://127.0.0.1:5001/health',timeout=2)
            if ready.status_code==200:break
        except requests.RequestException:pass
        time.sleep(1)
    else:raise RuntimeError('Parser service not ready after 30 checks')
    out=Path('evaluation/parser-benchmark/docling-smoke.json');results=[]
    for kind,mode in [('text','AUTO'),('text','STANDARD'),('table','STANDARD'),('scan','AUTO')]:
        started=time.perf_counter()
        try:
            r=requests.post('http://127.0.0.1:5001/parse-stream',files={'file':('synthetic.pdf',fixture(kind),'application/pdf')},data={'parse_mode':mode},timeout=(10,240));r.raise_for_status()
            events=[json.loads(l) for l in r.text.splitlines()];batches=[e for e in events if e['type']=='batch']
            assert events[-1]['type']=='complete',events[-1]
            assert batches and all(b['quality']['passed'] for b in batches)
            text='\n'.join(b['text'] for e in batches for b in e['blocks'])
            assert ('160' in text and 'credits' in text.lower()) if kind=='scan' else '123' in text
            if kind=='table':assert any(b['type']=='TABLE' and b['attributes'].get('table') for e in batches for b in e['documentIR']['blocks'])
            if kind=='scan':assert batches[0]['parserMode']=='DEEP'
            results.append({'fixture':kind,'requested_mode':mode,'synthetic':True,'status':'PASSED','elapsed_ms':(time.perf_counter()-started)*1000,'modes':[e['parserMode'] for e in batches],
                'attempts':[[{'mode':a['mode'],'quality':a['quality'],'modelInitMs':a['modelInitMs'],'parseDurationMs':a['parseDurationMs']} for a in e['attempts']] for e in batches]})
        except Exception as e:results.append({'fixture':kind,'synthetic':True,'status':'FAILED','error':str(e),'elapsed_ms':(time.perf_counter()-started)*1000})
        out.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(results[-1],ensure_ascii=False),flush=True)
    if any(r['status']!='PASSED' for r in results):raise SystemExit(1)
