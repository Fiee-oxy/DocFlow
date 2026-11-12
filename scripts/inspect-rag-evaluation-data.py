"""Inventory and import reference labels. This is NOT a pipeline quality evaluation."""
import collections
import json
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]/'evaluation/rag-quality'

def read_json(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def read_lines(path): return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
def write_lines(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')

def main():
    catalog=read_json(BASE/'sources.json')
    downloaded={item['id']:item for item in read_json(BASE/'download-manifest.json')['files'] if item['status']=='downloaded'}
    report={'llm_calls':0,'quality_evaluation_executed':False,'gold_review_complete':False,'documents':[],'datasets':{}}
    try: import pymupdf as fitz
    except ImportError: fitz=None
    for source in catalog['files']:
        if source['kind'] not in ['pdf','docx'] or source['id'] not in downloaded: continue
        path=BASE/source['path']
        item={k:source.get(k) for k in ['id','kind','path','language','domain','family_id','proposed_split','annotation_status']}
        item['sha256']=downloaded[source['id']]['sha256']
        if source['kind']=='docx':
            with zipfile.ZipFile(path) as archive: tree=ET.fromstring(archive.read('word/document.xml'))
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            item.update(paragraphs=len(tree.findall('.//w:p',ns)),tables=len(tree.findall('.//w:tbl',ns)),
                        text_characters=sum(len(node.text or '') for node in tree.findall('.//w:t',ns)),
                        locator='paragraph/table IDs; no stable page numbers')
        elif fitz:
            with fitz.open(path) as pdf:
                pages=sorted({0,min(1,len(pdf)-1),min(2,len(pdf)-1),len(pdf)//2,len(pdf)-1})
                item.update(pages=len(pdf),sample_page_text_chars={str(page+1):len(pdf[page].get_text()) for page in pages},
                            audit_note='Text-layer sampling only; not OCR/parser quality scoring')
        report['documents'].append(item)

    finance=read_lines(BASE/'raw/financebench/financebench_open_source.jsonl')
    normalized=[]
    for row in finance:
        evidence=[{'document_key':e.get('evidence_doc_name',e.get('doc_name',row['doc_name'])),
                   'physical_pages':[int(e['evidence_page_num'])+1],'source_page_zero_based':int(e['evidence_page_num']),
                   'quote':e['evidence_text'],'granularity':'PUBLIC_GOLD_REQUIRES_SPAN_REVIEW'} for e in row['evidence']]
        normalized.append({'case_id':row['financebench_id'],'source_dataset':'financebench','language':'en',
            'status':'IMPORTED_NEEDS_SOURCE_ALIGNMENT_AND_REVIEW','split':'UNASSIGNED','family_id':'company:'+row['company'],
            'question':row['question'],'answer_reference':row['answer'],'unanswerable':False,
            'document_scope':sorted({row['doc_name']}|{e['document_key'] for e in evidence}),
            'support_sets':[{'set_id':'public-gold','evidence':evidence}],'distractors':[],
            'provenance':{'revision':next(d['revision'] for d in catalog['datasets'] if d['id']=='financebench')}})
    write_lines(BASE/'annotations/financebench.imported.jsonl',normalized)
    report['datasets']['financebench']={'questions':len(finance),'primary_documents':len({r['doc_name'] for r in finance}),
        'all_evidence_documents':len({e['document_key'] for r in normalized for e in r['support_sets'][0]['evidence']}),
        'page_index_conversion':'source zero-based + 1 = physical page','reviewed_cases':0}

    qasper_splits={}
    with tarfile.open(BASE/'raw/qasper/qasper-train-dev-v0.3.tgz','r:gz') as archive:
        for member in archive:
            if member.isfile() and member.name.endswith('.json') and member.size<64*1024*1024:
                with archive.extractfile(member) as stream: dataset=json.load(stream)
                if not isinstance(dataset,dict): continue
                qasper_splits[member.name]={'papers':len(dataset),'questions':sum(len(p.get('qas',[])) for p in dataset.values())}
                if 'dev' not in Path(member.name).name: continue
                # Only a pilot selection. Do not mark it held out after debugging with it.
                selected_ids=list(dataset)[:6]
                for wanted in ['1912.01214','1810.08699']:
                    if wanted in dataset and wanted not in selected_ids: selected_ids.append(wanted)
                selected={key:dataset[key] for key in selected_ids}
                (BASE/'raw/qasper/pilot_full_text_and_labels.json').write_text(json.dumps(selected,ensure_ascii=False),encoding='utf-8')
                cases=[]
                for paper_id,paper in selected.items():
                    for qa in paper.get('qas',[]):
                        sets=[];answerability=[];references=[]
                        for index,annotation in enumerate(qa.get('answers',[])):
                            answer=annotation['answer'];answerability.append(not answer.get('unanswerable',False))
                            references.append(answer)
                            evidence=[{'document_key':paper_id,'physical_pages':[], 'quote':text,
                                'granularity':'PARAGRAPH_FROM_S2ORC','requires_visual':text.startswith('FLOAT SELECTED')}
                                for text in answer.get('evidence',[])]
                            if evidence: sets.append({'set_id':'annotator-'+str(index),'evidence':evidence})
                        cases.append({'case_id':qa['question_id'],'source_dataset':'qasper','language':'en',
                            'status':'IMPORTED_NEEDS_SOURCE_ALIGNMENT_AND_REVIEW','split':'UNASSIGNED',
                            'family_id':'paper:'+paper_id.split('v')[0],'document_scope':[paper_id],
                            'question':qa['question'],'answer_references':references,'support_sets':sets,'distractors':[],
                            'unanswerable':not any(answerability),'annotator_answerability_disagreement':len(set(answerability))>1,
                            'locator_note':'Use paragraph evidence for oracle lane; original PDF pages not mapped'})
                write_lines(BASE/'annotations/qasper.pilot.imported.jsonl',cases)
                report['datasets']['qasper_pilot']={'papers':len(selected),'questions':len(cases),'reviewed_cases':0,'pdf_pages_aligned':False}
    report['datasets']['qasper_archive']=qasper_splits

    omni=read_json(BASE/'raw/omnidocbench/OmniDocBench.json')
    report['datasets']['omnidocbench']={'annotated_pages':len(omni) if isinstance(omni,list) else None,
        'images_downloaded':0,'scope':'later OCR/image parser diagnostics; not born-digital PDF score'}
    mm=read_json(BASE/'raw/mmlongbench-doc/samples.json')
    mm_rows=mm if isinstance(mm,list) else next((v for v in mm.values() if isinstance(v,list)),[])
    report['datasets']['mmlongbench_doc']={'questions':len(mm_rows),'pdf_documents_downloaded':0,'page_anchors_reviewed':False}
    report['total_download_bytes']=sum(item['bytes'] for item in downloaded.values())
    (BASE/'preparation-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'documents':len(report['documents']),'formats':dict(collections.Counter(d['kind'] for d in report['documents'])),
        'languages':dict(collections.Counter(d['language'] for d in report['documents'])),
        'datasets':report['datasets'],'total_download_bytes':report['total_download_bytes'],
        'quality_evaluation_executed':False},ensure_ascii=True))

if __name__=='__main__': main()
