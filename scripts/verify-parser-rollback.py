"""Verify real legacy PDF/DOCX ingestion, indexing, and preview without chat LLM."""
import importlib.util,json,time,zipfile,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('pilot',ROOT/'scripts/run-rag-quality-pilot.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
OUT=ROOT/'evaluation/parser-rollback-20261018';OUT.mkdir(exist_ok=True)
docx=OUT/'rollback.docx'
with zipfile.ZipFile(docx,'w') as z:
    z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Graduation requirements</w:t></w:r></w:p><w:p><w:r><w:t>Rollback document evidence: graduation requires 160 credits.</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')
for attempt in range(60):
    try:session=p.load_session(OUT);break
    except Exception:time.sleep(1)
else:raise RuntimeError('Backend startup/login unavailable')
rows=[]
for key,path,answer in [('pdf',ROOT/'evaluation/parser-benchmark/synthetic/text.pdf','123'),('docx',docx,'160')]:
    begin=time.perf_counter();p.upload(OUT,session,{'id':'rollback-'+key,'path':str(path)})
    did=session['documents']['rollback-'+key]['document_id'];deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        doc=p.api('/documents/'+did,token=session['token'])
        if doc['status']=='FAILED':raise AssertionError(doc.get('errorMessage',str(doc)))
        if doc['parseComplete']:break
        time.sleep(1)
    else:raise AssertionError('Parse timeout')
    p.api('/documents/'+did+'/index',{},session['token'])
    while time.monotonic()<deadline:
        state=p.api('/documents/'+did+'/strategy',token=session['token'])
        if state['state']=='FAILED':raise AssertionError(state)
        if state['state']=='READY':break
        time.sleep(1)
    else:raise AssertionError('Index timeout')
    sql=p.sql("SELECT JSON_OBJECT('batches',COUNT(*),'ir_batches',COALESCE(SUM(JSON_CONTAINS_PATH(payload,'one','$.documentIR')),0),'docling_batches',COALESCE(SUM(JSON_CONTAINS_PATH(payload,'one','$.doclingDocument')),0)) FROM document_parse_batches WHERE document_id='"+did+"';")[0]
    assert sql['ir_batches']==0 and sql['batches']>0
    assert key!='docx' or sql['docling_batches']>0
    preview=p.api('/documents/query/preview',{'documentIds':[did],'question':'What is the revenue evidence?' if key=='pdf' else 'How many credits are required for graduation?','limit':100},session['token'])
    assert preview['llmCalls']==0 and any(answer in item.get('text','') for item in preview.get('evidence',[])),preview
    p.save(OUT/(key+'-preview.json'),preview)
    rows.append({'format':key,'parseComplete':True,'indexReady':True,'indexedChunks':state['indexedChunks'],
                 'irBatches':sql['ir_batches'],'doclingBatches':sql['docling_batches'],'evidenceFound':True,'llmCalls':0,'elapsed_ms':(time.perf_counter()-begin)*1000})
    p.save(OUT/'smoke-results.json',rows);print(json.dumps(rows[-1]),flush=True)
audit=p.audit(session);p.save(OUT/'no-llm-audit.json',audit)
assert audit['llmCalls']==0 and audit['queryTasks']==0
health=json.load(urllib.request.urlopen('http://127.0.0.1:5001/health'))
assert health['parseModes']==['FAST','DOCLING'] and 'adaptiveModes' not in health
p.save(OUT/'parser-health.json',health)
