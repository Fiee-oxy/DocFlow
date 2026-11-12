"""Sequential diagnostic controls so CPU contention does not distort comparisons."""
import json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evaluation/ocr-diagnosis-20261112'
D=r'C:/Program Files/Docker/Docker/resources/bin/docker.exe'
def main():
    for name in ['profile-docling-ocr.py','profile-tesseract-resolution.py']:
        subprocess.run([D,'cp',str(ROOT/'scripts'/name),'dovideo-docling:/tmp/'+name],check=True,capture_output=True)
    cases=[('fresh-process','profile-docling-ocr.py',['--out','/tmp/ocr-fresh-process.json','--runs','1']),
        ('no-table-setup','profile-docling-ocr.py',['--out','/tmp/ocr-no-table-setup.json','--runs','0','--no-table']),
        ('resolution','profile-tesseract-resolution.py',[])]
    summary=[]
    for name,script,args in cases:
        print('Starting '+name,flush=True);start=time.monotonic()
        run=subprocess.run([D,'exec','-e','PYTHONPATH=/app','dovideo-docling','python','-u','/tmp/'+script,*args],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=300)
        (OUT/(name+'.log')).write_text(run.stdout+run.stderr,encoding='utf-8')
        record=dict(case=name,exitCode=run.returncode,processWallSeconds=time.monotonic()-start);summary.append(record)
        (OUT/'case-status.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        if run.returncode:print(run.stderr[-2000:],flush=True)
        assert run.returncode==0,record
        subprocess.run([D,'cp','dovideo-docling:/tmp/ocr-'+name+'.json',str(OUT/(name+'.json'))],check=True,capture_output=True)
        print(json.dumps(record),flush=True)
if __name__=='__main__':main()
