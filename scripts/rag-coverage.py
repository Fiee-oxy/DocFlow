"""Order-independent lexical evidence diagnostics using real source adjacency."""
import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('lexical_score',Path(__file__).with_name('score-rag-quality-pilot.py'))
sc=importlib.util.module_from_spec(spec);spec.loader.exec_module(sc)

def canonical(rows,lookup):
    unique={}
    for i,row in enumerate(rows):
        key=row.get('chunk_id',row.get('id',''))
        text=row.get('content',row.get('text',''))
        order=lookup.get(key,row.get('blockIndex',row.get('block_index')))
        if order is None:order=1_000_000+i*2
        unique.setdefault((key,text),{**row,'content':text,'_order':int(order)})
    ordered=sorted(unique.values(),key=lambda r:(r.get('doc_id',''),r['_order']))
    previous=None;canonical=[]
    for row in ordered:
        marker=(row.get('doc_id',''),row['_order'])
        adjacent=previous is not None and marker[0]==previous[0] and marker[1]==previous[1]+1
        canonical.append({**row,'content':('' if adjacent else '\u241e')+row['content']})
        previous=marker
    return canonical

def coverage(rows,case,lookup):
    units=[]
    for unit in case['units']:
        eligible=[]
        for row in rows:
            pages=row.get('pages',[]) or [p for source in row.get('sources',[]) for p in source.get('pages',[])]
            if not unit['pages'] or set(unit['pages']).intersection(pages):eligible.append(row)
        units.extend(sc.coverage(canonical(eligible,lookup),{'units':[unit]})['units'])
    return {'complete':all(unit['complete'] for unit in units),'units':units}
