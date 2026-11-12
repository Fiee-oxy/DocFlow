"""Order-invariant evidence matching; never bridge an omitted source chunk."""
import re,unicodedata

def norm(text):
    return re.sub(r'[^\w\u3400-\u9fff]','',unicodedata.normalize('NFKC',text).casefold())

def coverage(items,case,source_order):
    items=list(items);details=[]
    for unit in case['units']:
        indexed={}
        for item in items:
            if not set(item.get('pages',[])) & set(unit['pages']):continue
            key=item.get('chunk_id') or item.get('id')
            if key not in source_order:raise ValueError(f'Unknown evidence source: {key}')
            index=source_order[key]
            text=item.get('text','')
            if index in indexed and indexed[index]!=text:raise ValueError('Conflicting text for same source')
            indexed[index]=text
        runs=[];last=None
        for index,text in sorted(indexed.items()):
            if last is not None and index==last+1:runs[-1]+='\n'+text
            else:runs.append(text)
            last=index
        normalized=[norm(text) for text in runs]
        details.append({'pages':unit['pages'],'atoms':[{'text':a,'found':any(norm(a) in run for run in normalized)} for a in unit['atoms']]})
    found=[a['found'] for unit in details for a in unit['atoms']]
    return {'complete':bool(found) and all(found),'atoms_found':sum(found),'atoms_total':len(found),
            'fraction':sum(found)/len(found) if found else 0,'units':details}

def source_order(chunks):
    return {chunk['chunk_id']:index for index,chunk in enumerate(chunks)}
