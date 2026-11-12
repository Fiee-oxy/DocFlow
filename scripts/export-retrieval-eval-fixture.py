"""Export index scope and chapter names only; never credentials or document body."""
import sys,uuid,json,subprocess
from pathlib import Path
doc=str(uuid.UUID(sys.argv[1]))
query=f"SELECT owner_id,index_version FROM document_adaptive_state WHERE document_id='{doc}'; SELECT JSON_OBJECT('section_id',section_id,'title',title,'title_path',title_path) FROM document_sections WHERE document_id='{doc}';"
r=subprocess.run(['docker','exec','-i','dovideo-ai-main-mysql-1','sh','-c','export MYSQL_PWD=$MYSQL_ROOT_PASSWORD; exec mysql -uroot --default-character-set=utf8mb4 --batch --raw --skip-column-names media_db'],input=query,text=True,encoding='utf-8',capture_output=True,check=True)
rows=r.stdout.strip().splitlines();owner,version=rows[0].split('\t')
path=Path('server/target/retrieval-eval-scope.json');path.write_text(json.dumps(dict(documentId=doc,ownerId=int(owner),version=version,sections=[json.loads(s) for s in rows[1:]]),ensure_ascii=False),encoding='utf-8')
print(str(path))
