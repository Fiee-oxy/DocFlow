"""Run database contracts in a disposable schema, never against media_db."""
import argparse,json,os,re,subprocess,uuid,xml.etree.ElementTree as E
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
name='rag_test_'+uuid.uuid4().hex[:12]
assert re.fullmatch(r'rag_test_[a-f0-9]{12}',name)
def sql(statement):
    subprocess.run(['docker','exec','-i','dovideo-ai-main-mysql-1','sh','-c','export MYSQL_PWD=$MYSQL_ROOT_PASSWORD; exec mysql -uroot'],input=statement,text=True,check=True,capture_output=True)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--tests');args=parser.parse_args()
    sql(f"CREATE DATABASE {name} CHARACTER SET utf8mb4; CREATE USER '{name}'@'%' IDENTIFIED BY ''; GRANT ALL ON {name}.* TO '{name}'@'%';")
    try:
        env=os.environ.copy();env.update(ADAPTIVE_TEST_DB_URL=f'jdbc:mysql://127.0.0.1:3307/{name}?allowPublicKeyRetrieval=true&useSSL=false',ADAPTIVE_TEST_DB_USER=name,ADAPTIVE_TEST_DB_PASSWORD='')
        env['JAVA_HOME']=r'C:\Program Files\Java\jdk-24'
        log=ROOT/'server/target/completion-acceptance-tests.log'
        with log.open('w',encoding='utf-8') as output:
            command=['mvn.cmd','-q']+(['-Dtest='+args.tests] if args.tests else [])+['test']
            result=subprocess.run(command,cwd=ROOT/'server',env=env,stdout=output,stderr=subprocess.STDOUT)
        files=list((ROOT/'server/target/surefire-reports').glob('TEST-*.xml'))
        report={key:sum(int(E.parse(f).getroot().get(key,0)) for f in files) for key in ['tests','failures','errors','skipped']}
        report['exitCode']=result.returncode;report['database']='Disposable isolated schema; removed after test'
        report['selection']=args.tests or 'all';report['countsNote']='Aggregated current Surefire reports; selection specifies the classes rerun now.'
        target=ROOT/'evaluation/rag-quality/outputs/knowledge-api-20261014/backend-tests.json';target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
        if result.returncode:raise SystemExit(result.returncode)
    finally:
        sql(f"DROP DATABASE {name}; DROP USER '{name}'@'%';")
if __name__=='__main__':main()
