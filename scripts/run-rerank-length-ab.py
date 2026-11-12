"""Paired frozen-candidate length ablation; no retrieval, index writes or chat LLM."""
import hashlib,importlib.util,json,os,sys,time
from pathlib import Path
from unittest.mock import patch
ROOT=Path('/work/evaluation/rerank-length-ab-20261029')

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

def save(result):
    temporary=ROOT/'results.tmp';temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');temporary.replace(ROOT/'results.json')

def coverage(hits,case,pdf,word):
    if case['scorer']=='word':return word.coverage(hits,case['gold'])['complete']
    return pdf.coverage(hits,case['gold'],case['lookup'])['complete']

def score_mode(r,question,candidates,mode,ceiling):
    import torch
    tokenizer,model=r.models['reranker']
    raw=[c.get('search_text') or c['content'] for c in candidates]
    lengths=[len(tokenizer(question,t,truncation=False)['input_ids']) for t in raw]
    start=time.perf_counter();visible=['']*len(raw);window_counts=[0]*len(raw);padded_slots=0;input_tokens=0
    if mode in {'TOKEN256','MODEL_CAP'}:
        texts=[r.truncate_for_rerank(question,t,tokenizer,256) for t in raw] if mode=='TOKEN256' else raw
        cap=256 if mode=='TOKEN256' else ceiling
        values=[]
        for offset in range(0,len(texts),8):
            batch=texts[offset:offset+8]
            encoded=tokenizer([[question,t] for t in batch],padding=True,truncation=True,max_length=cap,return_tensors='pt')
            padded_slots+=encoded['input_ids'].numel();input_tokens+=int(encoded['attention_mask'].sum())
            for j in range(len(batch)):
                ids=encoded['input_ids'][j].tolist();sequence=encoded.sequence_ids(j)
                visible[offset+j]=tokenizer.decode([v for v,s in zip(ids,sequence) if s==1],skip_special_tokens=True)
                window_counts[offset+j]=1
            with torch.inference_mode():values.extend(model(**encoded).logits.view(-1).float().tolist())
    else:
        # Every candidate token is covered. Long candidates use overlapping
        # windows, with max-logit aggregation; this is a distinct policy.
        overflow=tokenizer([[question,t] for t in raw],padding=False,truncation='only_second',
            max_length=ceiling,stride=32,return_overflowing_tokens=True)
        owners=overflow['overflow_to_sample_mapping'];examples=[];reconstructed=[[] for _ in raw]
        for j,i in enumerate(owners):
            piece=[v for v,s in zip(overflow['input_ids'][j],overflow.sequence_ids(j)) if s==1]
            reconstructed[i].extend(piece if window_counts[i]==0 else piece[32:])
            visible[i]+='\n'+tokenizer.decode(piece,skip_special_tokens=True)
            window_counts[i]+=1
            if window_counts[i]>32:raise ValueError('Full coverage window bound exceeded')
            examples.append({key:overflow[key][j] for key in ('input_ids','attention_mask','token_type_ids') if key in overflow})
        for i,text in enumerate(raw):
            assert reconstructed[i]==tokenizer(text,add_special_tokens=False)['input_ids'],'Full-window token coverage mismatch'
        values=[float('-inf')]*len(raw)
        for offset in range(0,len(examples),8):
            batch=examples[offset:offset+8]
            encoded={key:torch.nn.utils.rnn.pad_sequence([torch.tensor(x[key],dtype=torch.long) for x in batch],
                batch_first=True,padding_value=tokenizer.pad_token_id if key=='input_ids' else 0) for key in batch[0]}
            padded_slots+=encoded['input_ids'].numel();input_tokens+=int(encoded['attention_mask'].sum())
            assert encoded['input_ids'].shape[1]<=ceiling
            with torch.inference_mode():logits=model(**encoded).logits.view(-1).float().tolist()
            for i,value in zip(owners[offset:offset+8],logits):values[i]=max(values[i],value)
    elapsed=(time.perf_counter()-start)*1000
    return values,dict(score_ms=elapsed,raw_pair_tokens=lengths,overflow_model_limit=sum(n>ceiling for n in lengths),
        overflow_256=sum(n>256 for n in lengths),windows=window_counts,padded_token_slots=padded_slots,
        active_input_tokens=input_tokens,visible_text=visible)

def main():
    raw=(ROOT/'manifest.json').read_bytes();fingerprint=hashlib.sha256(raw).hexdigest()
    assert fingerprint==(ROOT/'manifest.sha256').read_text().strip()
    manifest=json.loads(raw)
    r=load('length_retrieval','/retrieval/app.py')
    assert not r.query_expander.enabled
    pdf=load('length_pdf_score','/work/scripts/rag-coverage.py')
    word=load('length_word_score','/work/scripts/evaluate-docx-holdout-retrieval.py')
    warm=time.perf_counter();r.rerank_scores('warmup',['warmup'])
    tokenizer,model=r.models['reranker']
    tokenizer_limit=tokenizer.model_max_length
    # XLM-R's position table includes the padding-index offset.
    position_limit=model.config.max_position_embeddings-model.config.pad_token_id-1
    ceiling=min(tokenizer_limit,position_limit)
    assert 256<=ceiling<=4096
    model(**tokenizer([['warmup','warmup '*700]]*8,padding=True,truncation=True,max_length=ceiling,return_tensors='pt'))
    result=json.loads((ROOT/'results.json').read_text()) if (ROOT/'results.json').exists() else {
        'manifest_sha256':fingerprint,'llmCalls':0,'scope':'Reranker only; same frozen RRF pool, no query/index/model-answer calls',
        'cases':[]}
    assert result['manifest_sha256']==fingerprint
    result['runtime']={'model':r.RERANKER,'tokenizer_model_max_length':tokenizer_limit,'position_table_size':model.config.max_position_embeddings,
        'effective_pair_limit':ceiling,'batch_size':8,'torch_threads':2,'warmup_ms':(time.perf_counter()-warm)*1000,
        'ordering':'Three arms rotated by case; one pass after warmup; 2 CPU / 5GB container',
        'full_coverage_policy':'Single full input if within limit; otherwise 32-token overlap windows + max logit; never silent truncation'}
    modes=['TOKEN256','MODEL_CAP','FULL_COVERAGE']
    for i,case in enumerate(manifest['cases']):
        record=next((x for x in result['cases'] if x['case_id']==case['case_id']),None)
        if record is None:
            record={k:case[k] for k in ('case_id','category','language','question')};record['arms']={}
            record['candidate_complete']=coverage(case['candidates'],case,pdf,word);result['cases'].append(record)
        ordered=modes[i%3:]+modes[:i%3]
        for mode in ordered:
            if mode in record['arms']:continue
            scores,detail=score_mode(r,case['question'],case['candidates'],mode,ceiling)
            with patch.object(r,'rerank_scores',return_value=scores):
                ranked=r.rerank(r.RerankRequest(question=case['question'],candidates=case['candidates'],
                    title_boost=case['title_boost'],focus_query=case['focus_query']))['candidates']
            visible=[c|{'content':t} for c,t in zip(case['candidates'],detail['visible_text'])]
            detail['input_evidence_complete']=coverage(visible,case,pdf,word)
            detail['complete_at']={str(k):coverage(ranked[:k],case,pdf,word) for k in (1,3,5,8,12,28)}
            detail['first_complete_rank']=next((k for k in range(1,len(ranked)+1) if coverage(ranked[:k],case,pdf,word)),None)
            detail['ranked']=ranked;record['arms'][mode]=detail;save(result)
            print(json.dumps({'case':case['case_id'],'mode':mode,'top12':detail['complete_at']['12'],
                'score_ms':round(detail['score_ms']),'overflow':detail['overflow_model_limit']}),flush=True)
    result['status']='COMPLETE';save(result)
if __name__=='__main__':main()
