"""Finish release checks after the frozen regression; no model-answer endpoint is called."""
import json,os,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];TARGET=ROOT/'server/target';RUN=ROOT/'evaluation/rag-quality/outputs/completion-ab-20261014'
def command(args,log,cwd=ROOT,env=None):
    with (TARGET/log).open('w',encoding='utf-8') as output:
        result=subprocess.run(args,cwd=cwd,env=env,stdout=output,stderr=subprocess.STDOUT)
    if result.returncode:raise RuntimeError(f'Failed check: {log}')
def main():
    print(json.dumps({'step':'waiting_for_90_question_regression'}),flush=True)
    deadline=time.monotonic()+7200
    while not (RUN/'no-llm-audit.json').exists():
        if time.monotonic()>deadline:raise TimeoutError('Regression did not finish')
        time.sleep(5)
    command(['python',str(ROOT/'scripts/review-rag-completion.py')],'completion-reviews.log')
    summary=json.loads((RUN/'summary.json').read_text(encoding='utf-8'));assert summary['tested']==90 and summary['ready']==90
    instances={json.loads(path.read_text(encoding='utf-8'))['instanceId'] for path in (RUN/'previews').glob('*.json')};assert len(instances)==1
    env=os.environ.copy();env['JAVA_HOME']=r'C:\Program Files\Java\jdk-24'
    command(['mvn.cmd','-q','-DskipTests','package'],'completion-release-build.log',ROOT/'server',env)
    command(['npm.cmd','run','build'],'completion-release-frontend.log',ROOT/'client')
    print(json.dumps({'step':'builds_passed_restarting_backend'}),flush=True)
    stop="""$backendId=Get-NetTCPConnection -State Listen -LocalPort 9090 | Select-Object -ExpandProperty OwningProcess -Unique;
    $info=Get-CimInstance Win32_Process -Filter \"ProcessId=$backendId\";
    if($info.CommandLine -notmatch 'com\\.example\\.server\\.[A-Za-z]+Application'){throw 'Unexpected backend process'};
    Stop-Process -Id $backendId;"""
    command(['powershell.exe','-NoProfile','-Command',stop],'completion-restart.log')
    log=(TARGET/'completion-release-backend.log').open('w',encoding='utf-8')
    process=subprocess.Popen(['powershell.exe','-NoProfile','-File',str(ROOT/'scripts/start-local-backend.ps1')],cwd=ROOT,
        stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
    (TARGET/'completion-release-process.json').write_text(json.dumps({'launcherPid':process.pid}),encoding='utf-8')
    ready=False
    for attempt in range(90):
        try:
            with urllib.request.urlopen('http://127.0.0.1:9090/health',timeout=2) as response:ready=response.status==200
            if ready:break
        except OSError:pass
        time.sleep(1)
    if not ready:raise RuntimeError('Released backend did not become ready')
    command(['python',str(ROOT/'scripts/test-knowledge-api.py')],'completion-release-api.log')
    command(['docker','cp',str(ROOT/'services/retrieval/test_focus_ranking.py'),'dovideo-retrieval:/retrieval/test_focus_ranking.py'],'completion-copy-unit.log')
    command(['docker','exec','dovideo-retrieval','python','-m','unittest','-q','test_focus_ranking'],'completion-python-unit.log')
    command(['docker','cp',str(ROOT/'services/retrieval/smoke_test.py'),'dovideo-retrieval:/retrieval/smoke_test.py'],'completion-copy-smoke.log')
    command(['docker','exec','dovideo-retrieval','python','smoke_test.py'],'completion-live-retrieval-smoke.log')
    (TARGET/'completion-holdout-ready.marker').write_text('Release, permission checks and live index isolation passed.\n',encoding='utf-8')
    print(json.dumps({'step':'release_checks_passed_holdout_enabled'}),flush=True)
if __name__=='__main__':main()
