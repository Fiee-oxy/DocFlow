"""Real MySQL tests using a disposable schema, never the application's tables."""
import json,os,re,shutil,subprocess,uuid,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evaluation/final-release-20261112'
DOCKER=r'C:/Program Files/Docker/Docker/resources/bin/docker.exe'
name='upload_integrity_test_'+uuid.uuid4().hex[:12];assert re.fullmatch(r'upload_integrity_test_[a-f0-9]{12}',name)
username='final_db_'+uuid.uuid4().hex[:12]
def sql(text):
    subprocess.run([DOCKER,'exec','-i','dovideo-ai-main-mysql-1','sh','-c','export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysql -uroot'],input=text,text=True,check=True,capture_output=True)
def run_one(test_class):
    global name,username
    name='upload_integrity_test_'+uuid.uuid4().hex[:12];username='final_db_'+uuid.uuid4().hex[:12]
    sql(f"CREATE DATABASE {name} CHARACTER SET utf8mb4; CREATE USER '{username}'@'%' IDENTIFIED BY ''; GRANT ALL ON {name}.* TO '{username}'@'%';")
    try:
        env=os.environ.copy();env['JAVA_HOME']=r'C:/Program Files/Java/jdk-24';env['PATH']=env['JAVA_HOME']+'/bin;'+env['PATH']
        for prefix in ['ADAPTIVE','UPLOAD']:
            env[prefix+'_TEST_DB_URL']=f'jdbc:mysql://127.0.0.1:3307/{name}?allowPublicKeyRetrieval=true&useSSL=false'
            env[prefix+'_TEST_DB_USER']=username;env[prefix+'_TEST_DB_PASSWORD']=''
        output=ROOT/'server/target-final-db'
        with (OUT/('database-'+test_class+'.log')).open('w',encoding='utf-8') as log:
            result=subprocess.run([shutil.which('mvn'),'-q','-Dbuild.output.root='+str(output),'-Dtest='+test_class,'test'],cwd=ROOT/'server',env=env,stdout=log,stderr=subprocess.STDOUT)
        reports=list((output/'surefire-reports').glob('TEST-*.'+test_class+'.xml'))
        assert len(reports)==1
        counts={key:sum(int(ET.parse(p).getroot().get(key,0)) for p in reports) for key in ['tests','failures','errors','skipped']}
        counts.update(exitCode=result.returncode,isolatedSchema=True,selection=test_class)
        print(json.dumps(counts),flush=True)
        assert result.returncode==0 and counts['skipped']==0
        return counts
    finally:
        sql(f"DROP DATABASE {name}; DROP USER '{username}'@'%';")
def main():
    reports=[]
    for test_class in ['AdaptiveRepositoryIntegrationTest','UploadIntegrityRepositoryIntegrationTest']:
        reports.append(run_one(test_class))
        (OUT/'database-tests.json').write_text(json.dumps(reports,indent=2),encoding='utf-8')
if __name__=='__main__':main()
