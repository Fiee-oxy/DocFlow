"""Isolated, actual Docling A/B evaluation. Run inside the parser container.

Only evaluation files are written. No database, index or chat LLM is used.
The control uses today's converter with continuity disabled, not a checkout of
every historical parser change. Historical outputs remain a separate baseline.
"""
import argparse
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, '/app')
from pipeline import task_events
from normalization import normalize
from docling_core.types.doc import DoclingDocument


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def run(document, mode, base, destination):
    folder=destination/document['id'];folder.mkdir(parents=True,exist_ok=True)
    source=Path(document['input'])
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    if digest!=document['sha256']:raise ValueError('Source SHA-256 mismatch')
    settings=copy.deepcopy(base)
    settings.update(parse_mode='DOCLING',continuity_enabled=mode=='new',resume_pages=0,
                    resume_batches=0,resume_state=None,heading='')
    started=time.perf_counter()
    record={'documentId':document['id'],'mode':mode,'sourceSha256':digest,
            'sourceBytes':source.stat().st_size,'pages':document['pages'],
            'firstBatchSeconds':None,'firstVisibleBatchSeconds':None,'elapsedSeconds':None,
            'batchCount':0,'blocks':0,'status':'RUNNING','llmCalls':0,'settings':settings,
            'timingBasis':'parser subprocess + boundary assembly + evaluation event persistence; no upload/index/LLM',
            'versions':{p:importlib.metadata.version(p) for p in ('docling','docling-core','torch','pypdfium2')}}
    save(folder/(mode+'-run.json'),record)
    heading=[]
    stream=task_events(source,settings)
    rawlog=(folder/(mode+'-raw-events.jsonl')).open('w',encoding='utf-8')
    try:
        with (folder/(mode+'-events.jsonl')).open('w',encoding='utf-8') as log:
            for event in stream:
                observed=time.perf_counter()-started
                log.write(json.dumps(event,ensure_ascii=False)+'\n');log.flush()
                if event['type']=='batch':
                    record['batchCount']+=1;record['blocks']+=len(event['blocks'])
                    if record['firstBatchSeconds'] is None:record['firstBatchSeconds']=observed
                    if event['blocks'] and record['firstVisibleBatchSeconds'] is None:record['firstVisibleBatchSeconds']=observed
                    # A separate raw normalization permits a same-conversion
                    # continuity ablation without pretending it is new inference.
                    raw=normalize(DoclingDocument.model_validate(copy.deepcopy(event['doclingDocument'])),heading,
                                  ocr_enabled=settings['ocr_enabled'])
                    heading=list(raw['headingState'])
                    raw.update(type='batch',startPage=event['startPage'],endPage=event['endPage'],
                               totalPages=event['totalPages'],doclingDocument=event['doclingDocument'])
                    rawlog.write(json.dumps(raw,ensure_ascii=False)+'\n');rawlog.flush()
                    print(json.dumps({'document':document['id'],'mode':mode,'pages':event['endPage'],
                                      'elapsedSeconds':round(observed,3)},ensure_ascii=True),flush=True)
                    save(folder/(mode+'-run.json'),record)
                elif event['type']=='complete':record['status']='COMPLETE'
                elif event['type']=='error':record.update(status='FAILED',error=event);break
    except Exception as error:
        record.update(status='FAILED',error={'type':type(error).__name__,'message':str(error)})
    finally:
        stream.close();rawlog.close()
        record['elapsedSeconds']=time.perf_counter()-started
        if record['status']=='RUNNING':record['status']='INCOMPLETE'
        save(folder/(mode+'-run.json'),record)
    print(json.dumps({'document':document['id'],'mode':mode,'status':record['status'],
                      'elapsedSeconds':round(record['elapsedSeconds'],3)},ensure_ascii=True),flush=True)
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--output',required=True)
    arguments=parser.parse_args()
    manifest=json.loads(Path(arguments.manifest).read_text(encoding='utf-8-sig'))
    out=Path(arguments.output);out.mkdir(parents=True,exist_ok=True)
    save(out/'method.json',manifest)
    rows=[]
    for index,document in enumerate(manifest['documents']):
        for mode in (['control','new'] if index%2==0 else ['new','control']):
            rows.append(run(document,mode,manifest['settings'],out));save(out/'runs.json',rows)
    if any(r['status']!='COMPLETE' for r in rows):sys.exit(1)


if __name__=='__main__':main()
