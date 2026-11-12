"""Versioned AST projection and batch-local parent/child retrieval units.

Original blocks remain immutable. Parents are read units, never embedded.
Cross-batch parents are intentionally not guessed or rewritten after publication.
"""
from copy import deepcopy
import hashlib
import json

REVISION="document-hierarchy-v1"
def digest(*parts):
    return hashlib.sha256(json.dumps(parts,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def ast_for(request):
    batch=request.batch;nodes=[];sections={};refs={}
    raw=batch.get('chunkingDocument') or batch.get('doclingDocument') or {}
    raw_items={x.get('self_ref'):x for kind in ('texts','tables','pictures') for x in raw.get(kind,[])}
    for order,block in enumerate(batch.get('blocks',[])):
        ref=block.get('ref') or f'block:{order}'
        if ref in refs:raise ValueError('AST_DUPLICATE_SOURCE_REFERENCE')
        heading=block.get('section') or '正文';path=heading.split(' / ')
        previous=None
        for i,title in enumerate(path):
            prefix=' / '.join(path[:i+1]);sid=digest(request.document_id,request.index_version,'section',prefix)
            sections.setdefault(sid,dict(node_id=sid,type='section',title=title,heading_path=path[:i+1],parent_id=previous))
            previous=sid
        node_id=digest(request.document_id,request.index_version,request.batch_index,ref,order)
        refs[ref]=node_id
        metadata=deepcopy(block);original=raw_items.get(ref,{})
        provenance=block.get('prov') or original.get('prov') or []
        if not provenance and block.get('bbox') and len(block.get('pages',[]))==1:
            provenance=[{'page_no':block['pages'][0],'bbox':block['bbox']}]
        boxes=[]
        for prov in provenance:
            box=prov.get('bbox');page=prov.get('page_no')
            if not box:continue
            size=(raw.get('pages',{}).get(str(page),{}).get('size') or block.get('page_size') or {})
            origin=box.get('coord_origin');normalized=None;transform=None
            if all(k in box for k in ('l','t','r','b')):
                if origin=='TOPLEFT':
                    normalized=[box[k] for k in ('l','t','r','b')];transform={'y_scale':1,'y_offset':0}
                elif origin=='BOTTOMLEFT' and size.get('height'):
                    h=size['height'];normalized=[box['l'],h-box['t'],box['r'],h-box['b']];transform={'y_scale':-1,'y_offset':h}
            boxes.append({'page':page,'bbox':normalized,'coordinate_system':'PAGE_UNITS_TOPLEFT' if normalized else None,
                'raw_bbox':deepcopy(box),'page_size':size,'transform':transform})
        pages=block.get('pages',[])
        nodes.append(dict(node_id=node_id,type=block.get('label','text'),text=block.get('text',''),
            heading_path=path,parent_id=previous,source_ref=ref,batch_index=request.batch_index,reading_order=order,reading_order_key=[request.batch_index,order],
            page_start=min(pages) if pages else None,page_end=max(pages) if pages else None,
            bbox_refs=boxes,confidence=block.get('confidence'),table_structure=deepcopy(block.get('table_data')),
            raw_metadata=metadata,raw_parser_object=deepcopy(original)))
    return dict(schema_version=REVISION,document_id=request.document_id,parse_version=request.index_version,
        parser_audit=deepcopy(batch.get('parserAudit')),quality_report=deepcopy(batch.get('qualityReport')),
        batch_index=request.batch_index,assembly_scope='PARSE_BATCH',sections=list(sections.values()),nodes=nodes)

def bundle_for(request,chunks,tokenizer):
    ast=ast_for(request);by_ref={n['source_ref']:n for n in ast['nodes']}
    # Docling can expose an object omitted from the normalized text view. Keep
    # its raw object rather than silently inventing a geometry or confidence.
    raw=request.batch.get('chunkingDocument') or request.batch.get('doclingDocument') or {}
    originals={x.get('self_ref'):x for k in ('texts','tables','pictures') for x in raw.get(k,[])}
    children=deepcopy(chunks);parents=[];current=[];key=None
    def tokens(text):return len(tokenizer.encode(text,add_special_tokens=True))
    def flush():
        nonlocal current
        if not current:return
        heading=current[0]['title_path'];text='\n'.join(c['content'] for c in current)
        pid=digest(REVISION,request.document_id,request.index_version,request.batch_index,[c['chunk_id'] for c in current])
        node_ids=list(dict.fromkeys(n for c in current for n in c['ast_node_ids']))
        fragments=[];seen=set()
        for c in current:
            for source in c['sources']:
                for fragment in source.get('fragments',[]) or [{'ref':source['ref'],'pages':source['pages'],'batch_index':request.batch_index}]:
                    marker=json.dumps(fragment,sort_keys=True,ensure_ascii=False)
                    if marker not in seen:seen.add(marker);fragments.append(deepcopy(fragment))
        pages=sorted({p for c in current for s in c['sources'] for p in s.get('pages',[])})
        source=dict(id=pid,text=text,section=heading,pages=pages,batch_index=request.batch_index,
            ref=', '.join(f.get('ref','') for f in fragments),fragments=fragments,
            ast_node_ids=node_ids,child_ids=[c['chunk_id'] for c in current],context_level='PARENT')
        parents.append(dict(parent_id=pid,section_id=current[0]['section_id'],parent_section_id=by_ref[current[0]['source_refs'][0]]['parent_id'],
            content=text,token_count=tokens(text),source=source,child_ids=source['child_ids'],ast_node_ids=node_ids,
            batch_index=request.batch_index,reading_order=len(parents)))
        for c in current:
            c['parent_id']=pid
            for s in c['sources']:
                s.update(parent_id=pid,ast_node_ids=c['ast_node_ids'],hierarchy_revision=REVISION)
        current=[]
    prepared=[]
    for child in children:
        headers=list(dict.fromkeys(str(cell.get('text','')).strip() for ref in child.get('source_refs',[])
            for cell in ((by_ref.get(ref,{}).get('table_structure') or {}).get('table_cells',[]))
            if cell.get('column_header') and str(cell.get('text','')).strip()))
        if headers and not all(header in child['content'] for header in headers):
            prefix=' | '.join(headers)
            reserve=tokens(child['title_path']+'\n'+prefix)+12
            if reserve<request.max_tokens-16:
                from chunk_strategies import boundary
                parts=boundary(child['content'],child['title_path']+'\n'+prefix,tokenizer,request.target_tokens,request.max_tokens)
                for piece in parts:
                    fragment=deepcopy(child);fragment['content']=prefix+'\n'+piece
                    fragment['search_text']=fragment['title_path']+'\n'+fragment['content']
                    fragment['token_count']=tokens(fragment['search_text']);fragment['table_headers']=headers
                    for s in fragment['sources']:s['text']=fragment['content']
                    prepared.append(fragment)
                continue
            child['table_header_context']='AST_ONLY_OVER_BUDGET'
            for s in child['sources']:s['table_header_context']='AST_ONLY_OVER_BUDGET'
        prepared.append(child)
    children=prepared
    for order,child in enumerate(children):
        child['order']=order
        source_refs=child.get('source_refs',[])
        if not source_refs:raise ValueError('HIERARCHY_SOURCE_REFERENCE_REQUIRED')
        for ref in source_refs:
            if ref in by_ref:continue
            original=originals.get(ref)
            if not original:raise ValueError('HIERARCHY_UNRESOLVED_SOURCE_REFERENCE: '+ref)
            node=dict(node_id=digest(request.document_id,request.index_version,request.batch_index,ref),type=original.get('label','text'),
                text=original.get('text',''),heading_path=child['title_path'].split(' / '),parent_id=None,source_ref=ref,
                reading_order=len(ast['nodes']),reading_order_key=[request.batch_index,len(ast['nodes'])],batch_index=request.batch_index,page_start=None,page_end=None,bbox_refs=[],confidence=None,
                table_structure=deepcopy(original.get('data')),raw_metadata={},raw_parser_object=deepcopy(original))
            ast['nodes'].append(node);by_ref[ref]=node
        if child['token_count']>512:raise ValueError('HIERARCHY_CHILD_EXCEEDS_EMBEDDING_BUDGET')
        child['chunk_id']=digest(REVISION,request.document_id,request.index_version,request.batch_index,child['order'],
            request.parent_target_tokens,request.parent_max_tokens,child['content'])
        child['config_fingerprint']=digest(child['config_fingerprint'],REVISION,request.parent_target_tokens,request.parent_max_tokens)[:16]
        for s in child['sources']:s['id']=child['chunk_id']
        child['ast_node_ids']=[by_ref[ref]['node_id'] for ref in source_refs]
        table_refs=tuple(ref for ref in source_refs if by_ref[ref]['type']=='table')
        child_key=(child['title_path'],'table' if table_refs else 'text',table_refs)
        joined='\n'.join([c['content'] for c in current]+[child['content']])
        if current and (key!=child_key or tokens(joined)>request.parent_max_tokens or tokens('\n'.join(c['content'] for c in current))>=request.parent_target_tokens):flush()
        key=child_key;current.append(child)
        if tokens(child['content'])>request.parent_max_tokens:raise ValueError('HIERARCHY_CHILD_EXCEEDS_PARENT_BUDGET')
    flush()
    return dict(chunks=children,structure=ast,parents=parents,hierarchy_revision=REVISION)
