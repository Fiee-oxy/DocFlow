"""Actual parsing of physical pages 33-36 of a 26 MiB PDF, then boundary ablation.

This is a page-window diagnostic, not a full-document run or a timing benchmark.
The production generator is closed after its first 4-page batch, reclaiming its
worker. Both views use exactly the same new native Docling conversion.
"""
import copy
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,'/app')
from pipeline import task_events
from boundary_stream import BoundaryAssembler
from continuity import source_fragments


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True)
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    key='perf-e50fb54a86ae5c36'
    out=Path('/tmp/boundary-quality-20261026')/key;out.mkdir(exist_ok=True,parents=True)
    source=Path('/tmp/boundary-quality-input')/(key+'.pdf')
    expected='a7ee71afecd9b2de3e1dbec7229a1241a3d543f30e917aec9b12123b0185308d'
    digest=hashlib.sha256(source.read_bytes()).hexdigest();assert digest==expected
    manifest=json.loads(Path('/tmp/boundary-quality-manifest.json').read_text())
    settings=dict(manifest['settings'],parse_mode='DOCLING',resume_pages=32,resume_batches=8,
                  resume_state=None,heading='',continuity_enabled=False)
    started=time.perf_counter();events=task_events(source,settings);raw=None
    try:
        for event in events:
            if event['type']=='error':raise RuntimeError(event)
            if event['type']=='batch':raw=event;break
    finally:events.close()
    assert raw and raw['startPage']==33 and raw['endPage']==36
    elapsed=time.perf_counter()-started
    for block in raw['blocks']:block['batch_index']=8
    view=BoundaryAssembler(dict(manifest['settings'],parse_mode='DOCLING',resume_pages=0,
                               resume_batches=0,resume_state=None,heading='',continuity_enabled=True)).process(copy.deepcopy(raw))
    raw_sources=Counter(canonical(f) for b in raw['blocks'] for f in source_fragments(b))
    visible=view['blocks']+([view['resumeState']['pending']] if view['resumeState']['pending'] else [])
    new_sources=Counter(canonical(f) for b in visible for f in source_fragments(b))
    def tables(blocks,page):return [b for b in blocks if b.get('label')=='table' and page in b['pages']]
    left=next(b for b in tables(raw['blocks'],34) if 'Installing balance valves' in b['text'])
    right=next(b for b in tables(raw['blocks'],35) if 'cooling water at the outlet' in b['text'])
    def headers(block):return [c['text'] for c in block['table_data']['table_cells'] if c.get('column_header')]
    def rows(block):return [c['text'] for c in block['table_data']['table_cells']
                           if c['start_col_offset_idx']==0 and c.get('text','').strip().isdigit()]
    merged=any('Installing balance valves' in b['text'] and 'cooling water at the outlet' in b['text'] for b in visible)
    report={'documentId':key,'sourceSha256':digest,'sourceBytes':source.stat().st_size,
            'originalDocumentPages':56,'selectedPhysicalPages':[33,34,35,36],
            'fullDocumentParsed':False,'chatLlmCalls':0,'pageWindowElapsedSeconds':elapsed,
            'timingComparableToFullDocument':False,'comparison':'same actual conversion, continuity off vs on',
            'sourceFragmentsConservedExactly':new_sources==raw_sources,
            'trueContinuationTableMerged':merged,'page34Headers':headers(left),'page35Headers':headers(right),
            'page34NumberedRows':rows(left),'page35NumberedRows':rows(right),
            'page35FirstDataRowMisclassifiedAsHeader':any(c.get('column_header') and c.get('text','').strip()=='14' for c in right['table_data']['table_cells']),
            'normalizedBlocks':len(raw['blocks']),'visibleNewBlocks':len(view['blocks']),
            'pendingSources':len(source_fragments(view['resumeState']['pending'])) if view['resumeState']['pending'] else 0}
    save(out/'window-control.json',raw);save(out/'window-new.json',view);save(out/'window-result.json',report)
    print(json.dumps(report,ensure_ascii=True),flush=True)


if __name__=='__main__':main()
