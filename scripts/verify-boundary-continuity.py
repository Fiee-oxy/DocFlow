"""Live 33-page boundary, resume, published index and retrieval checks; no chat calls."""
import hashlib
import argparse
import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services/docling'))
from test_fast_continuity import pdf_bytes, TITLE, FIRST, SECOND
RUN=ROOT/'evaluation/boundary-continuity-20261023'
spec=importlib.util.spec_from_file_location('pilot',ROOT/'scripts/run-rag-quality-pilot.py')
pilot=importlib.util.module_from_spec(spec);spec.loader.exec_module(pilot)


def main():
    arguments=argparse.ArgumentParser(description=__doc__)
    arguments.add_argument('--existing-document',help='Resume this verification against its already uploaded fixture')
    existing=arguments.parse_args().existing_document
    health=requests.get('http://127.0.0.1:5001/health',timeout=5).json()
    assert health.get('continuityVersion')=='continuity-v1'
    pages=[]
    suffix=uuid.uuid4().hex[:8]
    for number in range(1,32):
        lines=[(50,700,f'This synthetic page {number} contains complete background material for integrity testing {suffix}.')]
        if number==1:lines.insert(0,(50,745,TITLE))
        pages.append(lines)
    pages.append([(50,65,FIRST)])
    pages.append([(50,730,SECOND)])
    data=pdf_bytes(pages)
    def parse(resume=None):
        response=requests.post('http://127.0.0.1:5001/parse-stream',files={'file':('boundary.pdf',data)},
                               data={'parse_mode':'FAST',**(resume or {})},timeout=90)
        response.raise_for_status()
        events=[json.loads(line) for line in response.content.splitlines()]
        assert not any(e['type']=='error' for e in events),events
        assert events[-1]['type']=='complete'
        return [e for e in events if e['type']=='batch']
    original=parse()
    assert [e['endPage'] for e in original]==[32,33]
    assert original[0]['resumeState']['pending']['pages']==[32]
    assert not any(FIRST in b['text'] for b in original[0]['blocks'])
    resumed=parse({'resume_pages':'32','resume_batches':'1','resume_heading':TITLE,
                  'resume_state':json.dumps(original[0]['resumeState'],ensure_ascii=False)})
    assert resumed==original[1:]
    joined=next(b for b in original[1]['blocks'] if FIRST in b['text'])
    assert joined['pages']==[32,33] and SECOND in joined['text']
    print(json.dumps({'parserResume':'PASS','pages':33,'joinedPages':joined['pages']}),flush=True)
    session=pilot.load_session(RUN);headers={'Authorization':'Bearer '+session['token']}
    def api(path,body=None):return pilot.api(path,body,session['token'])
    if existing:
        did=existing
    else:
        upload=api('/documents/uploads/init',{'filename':'boundary-'+suffix+'.pdf','sizeBytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        did=upload['uploadId']
        r=requests.post('http://127.0.0.1:9090/documents/upload-chunk',headers=headers,
                        data={'uploadId':did,'chunkIndex':0,'sha256':hashlib.sha256(data).hexdigest()},
                        files={'file':('part.bin',data)},timeout=30);r.raise_for_status()
        r=requests.post('http://127.0.0.1:9090/documents/complete-upload',headers=headers,params={'uploadId':did},timeout=30)
        assert r.status_code==202
    for _ in range(90):
        status=api('/documents/'+did)
        assert status['status']!='FAILED',status
        if status['parseComplete']:break
        time.sleep(1)
    else:raise AssertionError('Live parse did not finish')
    assert status['parsedPages']==33
    options={'strategy':'legacy','targetTokens':350,'maxTokens':480,'minPreferredTokens':80,'mergePeers':True}
    before=api('/documents/'+did+'/chunk-preview',dict(options,batchIndex=0))
    after=api('/documents/'+did+'/chunk-preview',dict(options,batchIndex=1))
    assert not any(FIRST in c['content'] for c in before['chunks'])
    combined=next(c for c in after['chunks'] if FIRST in c['content'])
    assert SECOND in combined['content'] and combined['sources'][0]['pages']==[32,33]
    assert len(combined['sources'][0]['fragments'])==2
    api('/documents/'+did+'/index',{})
    for _ in range(120):
        state=api('/documents/'+did+'/strategy')
        assert state['state']!='FAILED',state
        if state['state']=='READY':break
        time.sleep(1)
    else:raise AssertionError('Live index did not finish')
    config=dict(line.split('=',1) for line in (ROOT/'.env').read_text(encoding='utf-8-sig').splitlines()
                if '=' in line and not line.lstrip().startswith('#'))
    key=config.get('DOCUMENT_RETRIEVAL_KEY','').strip().strip('"').strip("'")
    r=requests.post('http://127.0.0.1:5002/search',headers={'Authorization':'Bearer '+key} if key else {},json={
        'owner_id':session['owner'],'scopes':[{'document_id':did,'index_version':state['version']}],
        'question':'How many credits must students earn before graduation?','dense_top_k':20,'sparse_top_k':20},timeout=90)
    r.raise_for_status();hits=r.json()
    matched=[c for c in hits['dense']+hits['sparse'] if FIRST in c['content'] and SECOND in c['content']]
    assert matched and matched[0]['sources'][0]['pages']==[32,33]
    report={'llmCalls':0,'pages':33,'sourceBytes':len(data),'documentId':did,
            'parserResumeExact':True,'firstBatchExcludesHeldTail':True,'publishedChunkContainsBothPages':True,
            'sourceReferences':[f['ref'] for f in combined['sources'][0]['fragments']],
            'indexedBatches':state['indexedBatches'],'indexedChunks':state['indexedChunks'],
            'retrievalContainsCompleteBoundaryEvidence':True,'tableCoverage':'structured-core tests, not live table-inference accuracy'}
    pilot.save(RUN/'live-http.json',report)
    pilot.save(RUN/'live-boundary-chunks.json',after)
    print(json.dumps(report,ensure_ascii=True),flush=True)


if __name__=='__main__':main()
