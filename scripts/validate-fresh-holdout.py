"""Verify immutable corpus/labels without any retrieval or model calls."""
import collections,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evaluation/fresh-holdout-20261112'
def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    lock=read('freeze.json');cases=read('cases.json');queries=read('queries.json');manifest=read('manifest.json')
    docs={d['id']:d for d in manifest['documents']}
    assert digest(OUT/'cases.json')==lock['caseSha256']
    assert len(cases)==len(queries)==40 and len(docs)==8
    assert collections.Counter(c['language']+'/'+c['group'] for c in cases)==dict.fromkeys(('zh/small','zh/large','en/small','en/large'),10)
    assert all(set(q)=={'case_id','document_key','language','group','kind','question'} for q in queries)
    assert len(list((OUT/'documents').glob('*.pdf')))==8
    for d in docs.values():
        assert d['new_file_fingerprint'] and not d['prior_pdf_matches']
        assert digest(ROOT/d['path'])==d['sha256']
    for c,q in zip(cases,queries):
        assert c['case_id']==q['case_id'] and c['question']==q['question']
        doc=docs[c['document_key']];assert c['source_sha256']==doc['sha256'] and not c['scored']
        assert all(1<=p<=doc['pages'] for u in c['units'] for p in u['pages'])
        if c.get('requires_ocr'):assert digest(ROOT/c['visual_reference'])==c['visual_sha256']
    # Correct metadata left over from the earlier source discovery iteration.
    manifest['source_families']='CAC, NPC/MIIT, MEE, CATL, NIST, IETF, NASA, Euratom Supply Agency'
    manifest['ingested']=False;manifest['retrieval_scored']=False
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    validation=dict(status='PASS',documents=8,questions=40,nativeRequiredAtomChecks=166,scannedVisualQuestions=5,
        oldPdfFingerprintDuplicates=0,productionWrites=0,externalLlmCalls=0,scored=False,
        annotationStatus='PENDING_INDEPENDENT_HUMAN_REVIEW',sourceHashes={p:digest(ROOT/p) for p in
            ('scripts/prepare-fresh-holdout.py','scripts/freeze-fresh-holdout.py','scripts/validate-fresh-holdout.py')},
        caseSha256=lock['caseSha256'])
    (OUT/'validation.json').write_text(json.dumps(validation,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(validation))
if __name__=='__main__':main()
