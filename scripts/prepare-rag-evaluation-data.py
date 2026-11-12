"""Download small evaluation inputs only. No model execution, DB access or service mutations."""
import argparse
import hashlib
import json
import shutil
import ssl
import subprocess
import tarfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'evaluation/rag-quality'
MAX_FILE_BYTES = 64 * 1024 * 1024

def check(path, kind):
    if kind == 'json':
        json.loads(path.read_text(encoding='utf-8-sig'))
    elif kind == 'jsonl':
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.strip(): json.loads(line)
    elif kind == 'pdf':
        with path.open('rb') as stream:
            if not stream.read(1024).lstrip().startswith(b'%PDF-'): raise ValueError('Not a PDF')
    elif kind == 'docx':
        with zipfile.ZipFile(path) as archive:
            if 'word/document.xml' not in archive.namelist(): raise ValueError('Not a DOCX')
    elif kind == 'tgz':
        with tarfile.open(path, 'r:gz') as archive:
            if not any(m.name.endswith('.json') and m.isfile() for m in archive): raise ValueError('No dataset JSON')

def download(item):
    target = (BASE / item['path']).resolve()
    if BASE.resolve() not in target.parents: raise ValueError('Path escapes evaluation directory')
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix(target.suffix + '.part')
        try:
            request = urllib.request.Request(item['url'], headers={'User-Agent':'DOVideo-RAG-Evaluation/0.1'})
            try:
                with urllib.request.urlopen(request, timeout=25) as response, temporary.open('wb') as output:
                    total = 0
                    for block in iter(lambda:response.read(256*1024), b''):
                        total += len(block)
                        if total > MAX_FILE_BYTES: raise ValueError('File exceeds 64 MiB cap')
                        output.write(block)
            except urllib.error.URLError as error:
                # Windows curl uses the OS certificate store/TLS implementation. Verification stays on.
                if not isinstance(error.reason,ssl.SSLError) or not shutil.which('curl.exe'): raise
                subprocess.run(['curl.exe','--fail','--location','--silent','--show-error','--max-time','25',
                    '--max-filesize',str(MAX_FILE_BYTES),'--output',str(temporary),item['url']],
                    check=True,capture_output=True,timeout=30)
            check(temporary, item['kind'])
            temporary.replace(target)
        finally:
            if temporary.exists(): temporary.unlink()
    check(target, item['kind'])
    with target.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'id':item['id'],'path':item['path'],'source_url':item['url'],'status':'downloaded',
            'bytes':target.stat().st_size,'sha256':digest,'annotation_status':item.get('annotation_status','PUBLIC_DATA_NOT_REVIEWED')}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='*', help='Source IDs; default downloads the prepared starter bundle')
    args=parser.parse_args()
    sources=json.loads((BASE/'sources.json').read_text(encoding='utf-8'))
    selected=[item for item in sources['files'] if args.only is None or item['id'] in args.only]
    if args.only and set(args.only)-{item['id'] for item in selected}: parser.error('Unknown source ID')
    manifest_path=BASE/'download-manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {'files':[]}
    results={item['id']:item for item in manifest['files']}
    for item in selected:
        try:
            results[item['id']]=download(item)
            print(json.dumps({'id':item['id'],'status':'downloaded','bytes':results[item['id']]['bytes']}),flush=True)
        except Exception as error:
            results[item['id']]={'id':item['id'],'path':item['path'],'source_url':item['url'],'status':'failed','error':type(error).__name__+': '+str(error)}
            print(json.dumps({'id':item['id'],'status':'failed','error':str(error)}),flush=True)
        manifest={'prepared_at':datetime.now(timezone.utc).isoformat(),'llm_calls':0,'production_mutations':0,'files':list(results.values())}
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'downloaded':sum(i['status']=='downloaded' for i in results.values()),'failed':sum(i['status']=='failed' for i in results.values()),'manifest':str(manifest_path)}),flush=True)

if __name__=='__main__': main()
