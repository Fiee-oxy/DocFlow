"""Live module contracts, isolated test users/files, no chat LLM calls."""
import concurrent.futures,hashlib,importlib.util,json,os,time,urllib.error,urllib.parse
from pathlib import Path
import fitz
spec=importlib.util.spec_from_file_location('e',Path(__file__).with_name('final-e2e.py'))
e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
checks=[]
def check(name,detail=True):
    assert detail
    checks.append(dict(name=name,passed=True,detail=detail));e.save('module-checks.json',dict(checks=checks,complete=False))
    print(name,flush=True)
def denied(action,statuses):
    try:action()
    except urllib.error.HTTPError as failure:
        body=failure.read().decode();assert failure.code in statuses,(failure.code,body);return failure.code
    raise AssertionError('Request unexpectedly accepted')
def wait_complete(doc,token):
    until=time.monotonic()+240
    while time.monotonic()<until:
        record=e.api('/documents/'+doc,token=token)
        if record['status']=='FAILED':raise AssertionError(record)
        if record['parseComplete']:return record
        time.sleep(1)
    raise TimeoutError('Parse completion')
def upload(data,name,token,digest=None):
    body=dict(filename=name,sizeBytes=len(data),sha256=digest or hashlib.sha256(data).hexdigest())
    session=e.api('/documents/uploads/init',body,token)
    for i,offset in enumerate(range(0,len(data),session['chunkSizeBytes'])):e.upload_part(session['uploadId'],i,data[offset:offset+session['chunkSizeBytes']],token)
    e.complete(session['uploadId'],token);return session['uploadId']

def main():
    actors=json.loads((e.OUT/'session.private.json').read_text(encoding='utf-8'))['actors'];token=actors[0]['token'];other=actors[1]['token']
    check('anonymous_denied',denied(lambda:e.api('/documents'),{401}))
    # Actual multi-part source, not a mock or concatenated part hashes.
    pdf=fitz.open();page=pdf.new_page();pix=fitz.Pixmap(fitz.csRGB,1400,1400,os.urandom(1400*1400*3),False)
    page.insert_image(fitz.Rect(40,300,550,800),pixmap=pix)
    page.insert_textbox(fitz.Rect(40,40,550,290),'Reliable upload verification. Preserve progress and verify complete source bytes. '*15,fontsize=9)
    data=pdf.tobytes(deflate=False);assert len(data)>4*1024*1024
    body=dict(filename='final-multipart.pdf',sizeBytes=len(data),sha256=hashlib.sha256(data).hexdigest())
    session=e.api('/documents/uploads/init',body,token);doc=session['uploadId'];n=session['chunkSizeBytes'];assert session['totalChunks']==2
    check('bad_part_hash_rejected',denied(lambda:e.upload_part(doc,0,data[:n],token,'0'*64),{400,409,422}))
    assert e.api('/documents/upload-status?uploadId='+doc,token=token)==[];check('no_ack_after_bad_hash')
    e.upload_part(doc,0,data[:n],token);e.upload_part(doc,0,data[:n],token)
    assert e.api('/documents/upload-status?uploadId='+doc,token=token)==[0];check('duplicate_part_is_idempotent')
    check('incomplete_merge_rejected',denied(lambda:e.complete(doc,token),{400,409}))
    resumed=e.api('/documents/uploads/init',body,token);assert resumed['uploadId']==doc and resumed['completedChunks']==[0];check('pause_resume_from_mysql_receipt')
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        sessions=list(pool.map(lambda _:e.api('/documents/uploads/init',body,token),range(10)))
    assert {s['uploadId'] for s in sessions}=={doc};check('concurrent_same_content_reuses_one_upload')
    e.upload_part(doc,1,data[n:],token);e.complete(doc,token);record=wait_complete(doc,token)
    check('multipart_full_file_verification_and_parse',dict(bytes=len(data),documentId=doc,blocks=record['totalBlocks']))
    instant=e.api('/documents/uploads/init',body,token);assert instant['uploadId']==doc and instant['mode']=='INSTANT';check('trusted_file_dedup_instant')
    check('cross_owner_document_denied',denied(lambda:e.api('/documents/'+doc,token=other),{403,404}))
    other_session=e.api('/documents/uploads/init',body,other);assert other_session['mode']!='INSTANT' and other_session['uploadId']!=doc;check('no_cross_owner_instant_leak')
    # The entire source hash must independently match even when every part is valid.
    tiny=fitz.open();p=tiny.new_page();p.insert_text((40,60),'Bad whole hash fixture; valid per-part digest.')
    bad=upload(tiny.tobytes(),'final-wrong-whole-hash.pdf',token,'1'*64)
    until=time.monotonic()+90
    while time.monotonic()<until:
        failed=e.api('/documents/'+bad,token=token)
        if failed['status']=='FAILED':break
        time.sleep(.5)
    assert failed['status']=='FAILED',failed;check('whole_hash_mismatch_never_parses',dict(documentId=bad,stage=failed['stage'],message=failed['errorMessage']))
    # Native PDF text is absent: OCR must actually read the raster.
    scan=fitz.open();p=scan.new_page();p.insert_text((60,100),'OCR VERIFICATION ALPHA 7392',fontsize=20)
    raster=p.get_pixmap(matrix=fitz.Matrix(2,2));scanned=fitz.open();q=scanned.new_page();q.insert_image(q.rect,pixmap=raster)
    scanned_id=upload(scanned.tobytes(),'final-ocr.pdf',token);wait_complete(scanned_id,token)
    blocks=e.api('/documents/'+scanned_id+'/chunks?size=50',token=token)
    extracted=' '.join(b['text'] for b in blocks['chunks']);assert '7392' in extracted and 'ALPHA' in extracted.upper(),extracted
    check('scanned_pdf_real_ocr',dict(documentId=scanned_id,text=extracted[:300]))
    # A real Word table travels through the production parser, not the stream experiment.
    import io,zipfile
    binary=io.BytesIO();base,_=e.docx_fixture(8451)
    cell=lambda value:'<w:tc><w:p><w:r><w:t>'+value+'</w:t></w:r></w:p></w:tc>'
    table='<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="4000"/><w:gridCol w:w="4000"/></w:tblGrid><w:tr>'+cell('Setting')+cell('Value')+'</w:tr><w:tr>'+cell('Recovery code')+cell('TABLE-8451')+'</w:tr></w:tbl>'
    with zipfile.ZipFile(io.BytesIO(base)) as source,zipfile.ZipFile(binary,'w',zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            content=source.read(item.filename)
            if item.filename=='word/document.xml':content=content.replace(b'<w:body>',('<w:body>'+table).encode())
            target.writestr(item,content)
    word_id=upload(binary.getvalue(),'final-table.docx',token);wait_complete(word_id,token)
    table_blocks=e.api('/documents/'+word_id+'/chunks?size=50',token=token);assert 'TABLE-8451' in json.dumps(table_blocks)
    check('docx_table_production_parse',dict(documentId=word_id,blocks=table_blocks['totalBlocks']))
    preview=json.loads((e.OUT/'warmup-preview.json').read_text(encoding='utf-8'));assert preview['llmCalls']==0;check('preview_has_zero_chat_llm_calls')
    source=next(s for s in preview['evidence'] if s.get('parent_id'));version=source['index_version'];did=source['doc_id']
    parent=e.api('/documents/'+did+'/parents/'+source['parent_id']+'?'+urllib.parse.urlencode(dict(indexVersion=version,budgetBytes=6000)),token=token)
    check('published_parent_context_read',bool(parent))
    structure=e.api('/documents/'+did+'/structure?'+urllib.parse.urlencode(dict(indexVersion=version,limit=1)),token=token)
    assert structure['batches'][0]['nodes'];check('canonical_structure_published')
    check('parent_scope_denied',denied(lambda:e.api('/documents/'+did+'/parents/'+source['parent_id']+'?indexVersion='+version,token=other),{403,404}))
    bases=e.api('/knowledge-bases',token=token);assert sum(b['isDefault'] for b in bases)==1;check('personal_default_knowledge_base')
    check('knowledge_scope_intersection_denied',denied(lambda:e.api('/knowledge/search',dict(documentIds=[did],knowledgeBaseIds=['nonexistent'],question='test'),token),{400,403,404}))
    e.save('module-checks.json',dict(complete=True,checks=checks,chatLlmCalls=0))
    print(json.dumps(dict(checks=len(checks),allPassed=True)),flush=True)
if __name__=='__main__':main()
