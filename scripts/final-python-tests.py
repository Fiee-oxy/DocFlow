"""Run both Python suites against the deployed dependency environments."""
import argparse,io,json,re,subprocess,tarfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evaluation/final-release-20261112'
DOCKER=r'C:/Program Files/Docker/Docker/resources/bin/docker.exe'
def main():
    global OUT
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path);args=parser.parse_args()
    if args.out:OUT=args.out.resolve()
    OUT.mkdir(parents=True,exist_ok=True)
    archive=io.BytesIO()
    with tarfile.open(fileobj=archive,mode='w') as tar:
        for service in ['docling','retrieval']:
            for pattern in ['*.py','*.json','config/*.yml']:
                for path in (ROOT/'services'/service).glob(pattern):tar.add(path,arcname='services/'+service+'/'+str(path.relative_to(ROOT/'services'/service)).replace('\\','/'))
    reports=[]
    for service in ['docling','retrieval']:
        container='dovideo-'+service
        subprocess.run([DOCKER,'exec',container,'mkdir','-p','/tmp/final-verification'],check=True,capture_output=True)
        subprocess.run([DOCKER,'exec','-i',container,'tar','-xf','-','-C','/tmp/final-verification'],input=archive.getvalue(),check=True,capture_output=True)
        command=[DOCKER,'exec','-e','PYTHONPATH=/tmp/final-verification/services/docling:/tmp/final-verification/services/retrieval',
            '-w','/tmp/final-verification/services/'+service,container,'python','-m','unittest','discover','-v']
        result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace');log=result.stdout+result.stderr
        (OUT/('python-'+service+'.log')).write_text(log,encoding='utf-8')
        match=re.search(r'Ran (\d+) tests? in ([\d.]+)s',log)
        reports.append(dict(service=service,exitCode=result.returncode,tests=int(match[1]) if match else 0,
            seconds=float(match[2]) if match else None,skipped=len(re.findall(r'\.\.\. skipped ',log))))
        (OUT/'python-tests.json').write_text(json.dumps(reports,indent=2),encoding='utf-8');print(json.dumps(reports[-1]),flush=True)
        if result.returncode:print(log[-4500:],flush=True)
    assert all(r['exitCode']==0 and r['tests']>0 for r in reports)
if __name__=='__main__':main()
