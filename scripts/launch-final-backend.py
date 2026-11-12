"""Launch the verified JAR privately; never echo .env credentials."""
import json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evaluation/final-release-20261112'
def main():
    OUT.mkdir(parents=True,exist_ok=True);env=os.environ.copy()
    for line in (ROOT/'.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1);env[key.strip()]=value.strip().strip('\"').strip("'")
    env['SPRING_PROFILES_ACTIVE']='final'
    env['DOCUMENT_QUERY_REWRITE_ENABLED']='true' if '--rewrite' in sys.argv else env.get('DOCUMENT_QUERY_REWRITE_ENABLED','false')
    # Bound short verification answers; this is reported as a test configuration.
    if '--verification' in sys.argv:env['DOCUMENT_OUTPUT_TOKENS']='512'
    jar=ROOT/'server/target-final/server-0.0.1-SNAPSHOT.jar';assert jar.exists()
    log=(OUT/'backend.log').open('a',encoding='utf-8')
    process=subprocess.Popen(['C:/Program Files/Java/jdk-24/bin/java.exe','-Xms128m','-Xmx768m','-jar',str(jar),
        '--debug=false','--logging.level.root=INFO','--logging.level.org.springframework=INFO'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
    (OUT/'runtime.json').write_text(json.dumps(dict(pid=process.pid,jar=str(jar),profile='final',rewrite=env['DOCUMENT_QUERY_REWRITE_ENABLED'],outputTokens=env.get('DOCUMENT_OUTPUT_TOKENS','2048')),indent=2),encoding='utf-8')
    print('Backend launched; pid='+str(process.pid))
if __name__=='__main__':main()
