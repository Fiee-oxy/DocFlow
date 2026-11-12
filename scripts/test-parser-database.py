"""Run repository integration tests in a NEW isolated database, never media_db."""
import json,os,subprocess,shutil,uuid
name='parser_upgrade_test_'+uuid.uuid4().hex[:12]
container='dovideo-ai-main-mysql-1'
subprocess.run(['docker','start',container],check=True,capture_output=True)
info=json.loads(subprocess.check_output(['docker','inspect',container]))[0]
env=dict(s.split('=',1) for s in info['Config']['Env']);password=env['MYSQL_ROOT_PASSWORD']
def sql(statement):
    return subprocess.run(['docker','exec','-i',container,'sh','-c','export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysql -uroot'],input=statement,text=True,capture_output=True,check=True)
import time
for attempt in range(30):
    try:sql('SELECT 1;');break
    except subprocess.CalledProcessError:time.sleep(1)
else:raise RuntimeError('MySQL did not become ready')
sql('CREATE DATABASE '+name+' CHARACTER SET utf8mb4;')
try:
    child={**os.environ,'JAVA_HOME':r'C:\Program Files\Java\jdk-24','ADAPTIVE_TEST_DB_URL':'jdbc:mysql://127.0.0.1:3307/'+name+'?allowPublicKeyRetrieval=true&useSSL=false','ADAPTIVE_TEST_DB_USER':'root','ADAPTIVE_TEST_DB_PASSWORD':password}
    result=subprocess.run([shutil.which('mvn'),'-q','-Dtest=AdaptiveRepositoryIntegrationTest','test'],cwd='server',env=child)
    import xml.etree.ElementTree as ET
    from pathlib import Path
    report=ET.parse('server/target/surefire-reports/TEST-com.example.server.document.adaptive.AdaptiveRepositoryIntegrationTest.xml').getroot()
    Path('evaluation/parser-benchmark/database-test-summary.json').write_text(json.dumps({'exitCode':result.returncode,**{k:report.attrib[k] for k in ['tests','failures','errors','skipped']},'isolatedDatabase':True},indent=2),encoding='utf-8')
    raise SystemExit(result.returncode)
finally:
    sql('DROP DATABASE '+name+';')
    print('Removed isolated database '+name)
