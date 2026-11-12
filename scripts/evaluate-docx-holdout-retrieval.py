"""Frozen holdout replay using production HybridChunker, BGE, BM25 and reranker.

Fresh collection per document/variant. No upload, Java section planner, chat LLM
or final-context budget simulation. Recall means all necessary source anchors.
"""
import importlib.util,json,os,re,sys,time,uuid,hashlib,argparse,zipfile,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path('/work/evaluation/docx-holdout-20261027')
os.environ['MILVUS_URI']='http://dovideo-milvus:19530'
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m
TABLE_ROWS={}
TABLE_REFS={}
def compact(text):
    text=re.sub(r'\*\*(.*?)\*\*',r'\1',str(text),flags=re.S)
    return re.sub(r'[\s|]','',text).replace('<br>','')
def source_table_rows(manifest):
    w='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    for doc in manifest['documents']:
        with zipfile.ZipFile(Path('/work')/doc['path']) as archive:
            body=ET.fromstring(archive.read('word/document.xml')).find(w+'body')
            table_index=0
            for node,element in enumerate(body):
                if element.tag!=w+'tbl':continue
                TABLE_REFS[(doc['key'],node)]=f'#/tables/{table_index}'
                table_index+=1
                TABLE_ROWS[(doc['key'],node)]=[[compact(''.join(t.text or '' for t in cell.iter(w+'t')))
                    for cell in row.findall(w+'tc')] for row in element.findall(w+'tr')]
def table_source_matches(hit,key,node):
    if hit.get('body_node')==node:return True
    sources=hit.get('sources',[])
    fragments=hit.get('source_fragments',[])+[f for s in sources for f in s.get('fragments',[])]
    if any(f.get('body_node')==node for f in fragments):return True
    refs=' '.join([str(hit.get('ref',''))]+[str(s.get('ref','')) for s in sources])
    expected=TABLE_REFS.get((key,node))
    return expected is not None and expected in re.findall(r'#/tables/\d+',refs)
def coverage(hits,question):
    texts=[compact(h.get('content',h.get('text',''))) for h in hits]
    groups=[]
    for group in question['necessary']:
        # Each anchor must occur within one unit, but separate anchors may be
        # supplied by different chunks. Never require unchanged chunk identity.
        anchors=[]
        for anchor in group['anchors']:
            if group['source_type']=='tbl':
                rows=TABLE_ROWS.get((question['document'],group['body_node']),[])
                matched=[cells for cells in rows if compact(anchor) in ''.join(cells)]
                # Word row/cell gold avoids treating HybridChunker serialization
                # punctuation or markdown emphasis as lost table evidence.
                anchors.append(any(all(cell in text for cell in cells if cell) for cells in matched
                    for hit,text in zip(hits,texts) if table_source_matches(hit,question['document'],group['body_node'])))
            else:anchors.append(any(compact(anchor) in text for text in texts))
        groups.append(all(anchors))
    return {'complete':all(groups),'groups':groups}
def fuse(dense,sparse,k=60,limit=28):
    scores={};values={}
    for route in (dense,sparse):
        seen=set();rank=0
        for hit in route:
            key=hit['chunk_id']
            if key in seen:continue
            rank+=1;seen.add(key);values.setdefault(key,hit);scores[key]=scores.get(key,0)+1/(k+rank)
    order=sorted(values,key=lambda key:(-scores[key],key));protected=set()
    for route in (dense,sparse):protected.update(h['chunk_id'] for h in route[:min(5,limit//2)])
    for key in order:
        if len(protected)>=limit:break
        protected.add(key)
    return [values[key]|{'rrf_score':scores[key]} for key in order if key in protected]
def save(report):
    (ROOT/'retrieval-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
def main():
    options=argparse.ArgumentParser();options.add_argument('--diagnostics-only',action='store_true');options.add_argument('--rescore-only',action='store_true');args=options.parse_args()
    goldbytes=(ROOT/'questions.json').read_bytes()
    assert hashlib.sha256(goldbytes).hexdigest()==(ROOT/'questions.sha256').read_text().strip()
    gold=json.loads(goldbytes);manifest=json.loads((ROOT/'manifest.json').read_text())
    source_table_rows(manifest)
    r=load('holdout_retrieval',Path('/retrieval/app.py'))
    report={'llmCalls':0,'gold_sha256':hashlib.sha256(goldbytes).hexdigest(),
        'scope':'Production retrieval subsystem; no Java planner or final-context claims',
        'parameters':{'dense':30,'bm25':30,'rrf_k':60,'fusion':28,'single_route_reserve':5,'rerank_top':12,
          'rerank_tokens':r.RERANK_MAX_TOKENS,'chunk_target':350,'chunk_max':480,'embedding':r.EMBEDDING,'reranker':r.RERANKER},'runs':[]}
    if args.diagnostics_only or args.rescore_only:
        existing=json.loads((ROOT/'retrieval-results.json').read_text())
        assert existing['gold_sha256']==report['gold_sha256'] and existing['parameters']==report['parameters']
        report=existing
    warm=time.perf_counter();vector=None
    if not args.rescore_only:
        vector=r.encode(['warmup'])[0];r.rerank_scores('warmup',['warmup']);report['warmup_ms']=(time.perf_counter()-warm)*1000
    report['coverage_revision']='source-row-cells-and-provenance-v3'
    if args.diagnostics_only or args.rescore_only:
        for previous in report['runs']:
            if previous.get('status')!='COMPLETE':continue
            key,mode=previous['document'],previous['mode']
            events=[json.loads(line) for line in (ROOT/key/f'{mode}-events.jsonl').read_text().splitlines()]
            blocks=[b for e in events for b in e.get('blocks',[])];chunks=[]
            for event in events:
                if event['type']=='batch':chunks.extend(r.chunks_for(r.ChunkRequest(document_id='rescore',owner_id=991027,index_version='holdout-v1',batch_index=event.get('batchIndex',0),batch=event)))
            (ROOT/key/f'{mode}-chunks.json').write_text(json.dumps(chunks,ensure_ascii=False),encoding='utf8')
            for result in previous['questions']:
                q=next(q for q in gold['questions'] if q['id']==result['id'])
                result.setdefault('coverage_v1',result['coverage'])
                stages={'parse':blocks,'chunk':chunks,'dense':result['dense'],'bm25':result['bm25'],
                    'union':result['dense']+result['bm25'],'fusion':result['fusion'],'rerank12':result['reranked'][:12]}
                result['coverage']={name:coverage(value,q) for name,value in stages.items()}
        save(report)
        if args.rescore_only:return
    for doc in manifest['documents']:
        questions=[q for q in gold['questions'] if q['document']==doc['key']]
        run=json.loads((ROOT/doc['key']/'run.json').read_text())
        for mode in ('docling','stream','boundary','stream_diagnostic','boundary_diagnostic'):
            if args.diagnostics_only and not mode.endswith('_diagnostic'):continue
            if mode not in run:continue
            row={'document':doc['key'],'mode':mode,'parse_status':run[mode]['status'],'questions':[],'cleanup':False}
            report['runs'].append(row)
            row['diagnostic_only']=mode.endswith('_diagnostic')
            row['eligible_for_ingestion']=row['parse_status']=='COMPLETE' and not row['diagnostic_only']
            if row['parse_status']!='COMPLETE' and not (row['diagnostic_only'] and row['parse_status']=='PARTIAL_UNSUPPORTED'):
                row['status']='NOT_INGESTIBLE';save(report);continue
            events=[json.loads(line) for line in (ROOT/doc['key']/f'{mode}-events.jsonl').read_text().splitlines()]
            r.COLLECTION='docx_holdout_'+uuid.uuid4().hex[:20];row['collection']=r.COLLECTION
            identifier=str(uuid.uuid4());allchunks=[];blocks=[];row['index_ms']=0;row['wire_max_bytes']=max(len(json.dumps(e,ensure_ascii=False).encode()) for e in events)
            try:
                r.ensure_collection(len(vector))
                for e in events:
                    if e['type']!='batch':continue
                    blocks.extend(e['blocks']);start=time.perf_counter()
                    indexed=r.index(r.ChunkRequest(document_id=identifier,owner_id=991027,index_version='holdout-v1',batch_index=e.get('batchIndex',0),batch=e))
                    row['index_ms']+=(time.perf_counter()-start)*1000;allchunks.extend(indexed['chunks'])
                row['chunks']=len(allchunks);row['blocks']=len(blocks)
                (ROOT/doc['key']/f'{mode}-chunks.json').write_text(json.dumps(allchunks,ensure_ascii=False),encoding='utf8')
                row['boundary']=[e.get('docxBoundary') for e in events if e.get('docxBoundary')]
                for q in questions:
                    start=time.perf_counter()
                    hits=r.search(r.SearchRequest(owner_id=991027,scopes=[{'document_id':identifier,'index_version':'holdout-v1'}],question=q['question'],dense_top_k=30,sparse_top_k=30))
                    search_ms=(time.perf_counter()-start)*1000;start=time.perf_counter()
                    fused=fuse(hits['dense'],hits['sparse']);fusion_ms=(time.perf_counter()-start)*1000
                    start=time.perf_counter();ranked=r.rerank(r.RerankRequest(question=q['question'],candidates=fused))['candidates'];rerank_ms=(time.perf_counter()-start)*1000
                    stages={'parse':blocks,'chunk':allchunks,'dense':hits['dense'],'bm25':hits['sparse'],
                        'union':hits['dense']+hits['sparse'],'fusion':fused,'rerank12':ranked[:12]}
                    result={'id':q['id'],'question':q['question'],'language':q['language'],
                        'coverage':{name:coverage(value,q) for name,value in stages.items()},
                        'timing_ms':{'search':search_ms,'rrf':fusion_ms,'rerank':rerank_ms,'total':search_ms+fusion_ms+rerank_ms},
                        'dense':hits['dense'],'bm25':hits['sparse'],'fusion':fused,'reranked':ranked}
                    row['questions'].append(result);save(report)
                row['status']='COMPLETE'
                print(json.dumps({'document':doc['key'],'mode':mode,'chunks':len(allchunks),'rerank_complete':sum(q['coverage']['rerank12']['complete'] for q in row['questions']),'questions':len(questions)}),flush=True)
            except Exception as error:
                row['status']='ERROR';row['error']=type(error).__name__+': '+str(error);raise
            finally:
                if r.milvus().has_collection(r.COLLECTION):r.milvus().drop_collection(r.COLLECTION)
                row['cleanup']=True;save(report)
    save(report)
if __name__=='__main__':main()
