"""Isolated diagnostic process: instrument the installed Docling, not the server.

Run inside the parser container with PYTHONPATH=/app. No ingestion/index writes.
Timings are nested: factory/network/CLI durations must not be summed together.
"""
import time
SCRIPT_START=time.perf_counter()
import argparse,hashlib,json,os,threading,subprocess
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);parser.add_argument('--runs',type=int,default=1)
    parser.add_argument('--no-table',action='store_true');args=parser.parse_args()
    result={'variant':'no-table' if args.no_table else 'current','pid':os.getpid(),'runs':[],'network':[],'factoryInitializations':[]}
    output=Path(args.out)
    def save():
        result['scriptElapsedSeconds']=time.perf_counter()-SCRIPT_START
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    # Record only public hostnames and elapsed time, never tokens/headers/body.
    import urllib.parse,httpx,requests
    original_httpx=httpx.Client.send;original_requests=requests.Session.send
    def httpx_send(self,request,*a,**kw):
        start=time.perf_counter();status=None
        try:
            response=original_httpx(self,request,*a,**kw);status=response.status_code;return response
        finally:result['network'].append(dict(host=request.url.host,method=request.method,status=status,seconds=time.perf_counter()-start))
    def requests_send(self,request,*a,**kw):
        start=time.perf_counter();status=None
        try:
            response=original_requests(self,request,*a,**kw);status=response.status_code;return response
        finally:result['network'].append(dict(host=urllib.parse.urlparse(request.url).hostname,method=request.method,status=status,seconds=time.perf_counter()-start))
    httpx.Client.send=httpx_send;requests.Session.send=requests_send
    start=time.perf_counter()
    from ocr_config import create_converter
    from normalization import normalize
    from docling.datamodel.settings import settings
    from docling.datamodel.base_models import InputFormat
    from docling.models.factories.base_factory import BaseFactory
    from docling.models.stages.ocr.tesseract_ocr_cli_model import TesseractOcrCliModel
    import torch
    from PIL import Image
    result['importsSeconds']=time.perf_counter()-start
    settings.debug.profile_pipeline_timings=True
    result['hardware']=dict(cudaAvailable=torch.cuda.is_available(),torchThreads=torch.get_num_threads(),ompThreads=os.getenv('OMP_NUM_THREADS'))
    original_factory=BaseFactory.create_instance
    def create_instance(self,options,**kwargs):
        start=time.perf_counter();model=None
        try:model=original_factory(self,options,**kwargs);return model
        finally:result['factoryInitializations'].append(dict(options=type(options).__name__,model=type(model).__name__,enabled=kwargs.get('enabled'),seconds=time.perf_counter()-start))
    BaseFactory.create_instance=create_instance
    context=threading.local();patched_backends=set();current={'commands':[],'renders':[]}
    original_ocr=TesseractOcrCliModel.__call__
    def profiled_ocr(self,conv_res,pages):
        pages=list(pages)
        for page in pages:
            backend=type(page._backend)
            if backend in patched_backends:continue
            patched_backends.add(backend);original=backend.get_page_image
            def render(instance,*a,_original=original,**kw):
                start=time.perf_counter();image=_original(instance,*a,**kw)
                if getattr(context,'ocr',False):current['renders'].append(dict(scale=kw.get('scale',a[0] if a else None),pixels=list(image.size),seconds=time.perf_counter()-start))
                return image
            backend.get_page_image=render
        context.ocr=True
        try:yield from original_ocr(self,conv_res,pages)
        finally:context.ocr=False
    TesseractOcrCliModel.__call__=profiled_ocr
    original_run=subprocess.run
    def run(command,*a,**kw):
        if not isinstance(command,(list,tuple)) or not command or Path(str(command[0])).name!='tesseract':return original_run(command,*a,**kw)
        kind='recognition' if 'tsv' in command else 'orientation' if '--psm' in command and str(command[command.index('--psm')+1])=='0' else 'probe'
        entry=dict(kind=kind)
        image_path=next((Path(str(value)) for value in command if str(value).endswith('.png')),None)
        if image_path is not None and image_path.exists():
            with Image.open(image_path) as image:entry['pixels']=list(image.size)
            entry['imageSha256']=hashlib.sha256(image_path.read_bytes()).hexdigest()
        start=time.perf_counter()
        try:
            process=original_run(command,*a,**kw);entry['exitCode']=process.returncode;return process
        except subprocess.CalledProcessError as error:entry['exitCode']=error.returncode;raise
        finally:entry['seconds']=time.perf_counter()-start;current['commands'].append(entry)
    subprocess.run=run
    start=time.perf_counter()
    converter=create_converter(dict(ocr_enabled=True,ocr_languages=['chi_sim','eng'],table_mode='fast',threads=2,timeout=180))
    if args.no_table:converter.format_to_options[InputFormat.PDF].pipeline_options.do_table_structure=False
    result['converterConstructionSeconds']=time.perf_counter()-start
    result['pipelineOptions']=converter.format_to_options[InputFormat.PDF].pipeline_options.model_dump(mode='json')
    start=time.perf_counter();converter.initialize_pipeline(InputFormat.PDF)
    result['pipelineInitializationSeconds']=time.perf_counter()-start
    pipeline=next(iter(converter.initialized_pipelines.values()))
    result['ocrScale']=pipeline.ocr_model.scale
    result['modelTypes']={name:type(getattr(pipeline,name)).__name__ for name in ['ocr_model','layout_model','table_model']}
    result['setupElapsedSeconds']=time.perf_counter()-SCRIPT_START;save()
    for index in range(args.runs):
        current={'commands':[],'renders':[]};start=time.perf_counter()
        converted=converter.convert('/tmp/docflow-ocr-diagnostic.pdf',page_range=(1,1),max_num_pages=1000,max_file_size=209715200)
        conversion_seconds=time.perf_counter()-start;start=time.perf_counter()
        parsed=normalize(converted.document,ocr_enabled=True)
        text='\n'.join(block['text'] for block in parsed['blocks'])
        result['runs'].append(dict(index=index,conversionSeconds=conversion_seconds,normalizationSeconds=time.perf_counter()-start,
            status=str(converted.status),blocks=len(parsed['blocks']),textCharacters=len(text),textSha256=hashlib.sha256(text.encode()).hexdigest(),
            tables=len(converted.document.tables),commands=current['commands'],renders=current['renders'],
            doclingTimings={name:dict(seconds=float(item.total()),count=item.count,samples=item.times) for name,item in converted.timings.items()}))
        save();print(json.dumps(dict(variant=result['variant'],run=index,conversionSeconds=conversion_seconds,blocks=len(parsed['blocks']))),flush=True)
    save()
if __name__=='__main__':main()
