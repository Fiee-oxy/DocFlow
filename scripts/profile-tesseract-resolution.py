"""Isolated raster-size / direct Tesseract comparison; no production routing change."""
import difflib,hashlib,json,subprocess,tempfile,time
from pathlib import Path
from contextlib import closing
import pypdfium2 as pdfium

def main():
    result={'runs':[],'note':'Full-page direct OCR; agreement is not ground-truth accuracy. Docling crop segmentation differs.'};texts=[]
    with tempfile.TemporaryDirectory(prefix='docflow-ocr-resolution-') as directory,pdfium.PdfDocument('/tmp/docflow-ocr-diagnostic.pdf') as pdf:
        for scale in [3.0,2.0]:
            start=time.perf_counter();path=Path(directory)/('scale-'+str(scale)+'.png')
            with closing(pdf[0]) as page:
                bitmap=page.render(scale=scale);image=bitmap.to_pil();pixels=list(image.size);image.save(path);bitmap.close()
            render_seconds=time.perf_counter()-start;start=time.perf_counter()
            process=subprocess.run(['tesseract','-l','chi_sim+eng',str(path),'stdout','tsv'],check=True,capture_output=True,timeout=90)
            recognize_seconds=time.perf_counter()-start
            import csv,io
            words=[row['text'] for row in csv.DictReader(io.StringIO(process.stdout.decode('utf-8')),delimiter='\t') if row.get('text','').strip()]
            text=''.join(words);texts.append(text)
            result['runs'].append(dict(scale=scale,dpi=scale*72,pixels=pixels,renderSeconds=render_seconds,recognitionSeconds=recognize_seconds,
                textCharacters=len(text),textSha256=hashlib.sha256(text.encode()).hexdigest(),wordRows=len(words)))
    result['textAgreement']=difflib.SequenceMatcher(None,*texts,autojunk=False).ratio()
    Path('/tmp/ocr-resolution.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)
if __name__=='__main__':main()
