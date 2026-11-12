"""Freeze question types and saved RRF pools before length A/B inference."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'evaluation/rerank-length-ab-20261029'
SELECTION={
 'SINGLE_FACT':['perf-2cbcdc1cd99a7c58-q1','perf-2cbcdc1cd99a7c58-q3','perf-c6e7e73466012702-q3','perf-ae8d93d26bcd8614-q3'],
 'MULTI_FACT':['perf-8f184867d0ab755a-q1','perf-4d17e2eb6ea2e3ee-q3','perf-142acd18b6ae17a5-q1','perf-1e184f23111d760c-q2'],
 'LIST_CONDITIONS':['perf-ae8d93d26bcd8614-q2','perf-520c59b0bb11e7ae-q3','perf-771e4ad5d212cdf4-q3','perf-5a0063cbe381a1f9-q3'],
 'EXPLANATION_MECHANISM':['perf-8f184867d0ab755a-q3','perf-eac932022de3335e-q2','perf-9fca487b4d75d397-q2','word-en-03'],
 'COMPARISON_CHANGE':['perf-b5f4932fabc55e64-q3','perf-0805dd371db17d0a-q2','perf-67ab5de4a1c922f8-q2','word-en-09']}

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'manifest.json').exists():raise FileExistsError('Frozen inputs already exist')
    base=ROOT/'evaluation/rag-quality/outputs/fixed-top30-rerank256-20261016'
    cases={c['case_id']:c for c in json.loads((base/'cases.json').read_text(encoding='utf8'))}
    word=ROOT/'evaluation/docx-holdout-20261027'
    wordcases={q['id']:q for q in json.loads((word/'questions.json').read_text(encoding='utf8'))['questions']}
    wordrun=next(r for r in json.loads((word/'retrieval-results.json').read_text(encoding='utf8'))['runs'] if r['document']=='en_cargo_bikes' and r['mode']=='docling')
    entries=[]
    for category,ids in SELECTION.items():
        for key in ids:
            if key.startswith('word-'):
                qid=key[5:];gold=wordcases[qid];q=next(q for q in wordrun['questions'] if q['id']==qid)
                entry=dict(case_id=key,category=category,language='en',question=gold['question'],gold=gold,scorer='word',lookup={},
                    candidates=q['fusion'],title_boost=True,focus_query='',source='docx-holdout-20261027 / en_cargo_bikes / docling')
            else:
                gold=cases[key];preview=json.loads((base/'previews'/f'{key}.json').read_text(encoding='utf8'));trace=preview['retrieval'][0]
                chunks=json.loads((ROOT/'evaluation/rag-quality/outputs/recall-20261014/documents'/gold['document_key']/'rag_chunks.json').read_text(encoding='utf8'))
                lookup={c['chunk_id']:c.get('blockIndex',c.get('block_index',i)) for i,c in enumerate(chunks)}
                entry=dict(case_id=key,category=category,language=gold['language'],question=gold['question'],gold=gold,scorer='pdf',lookup=lookup,
                    candidates=trace['rrf'],title_boost=trace.get('titleBoost',True),focus_query='' if trace.get('titleBoost',True) else trace.get('searchQuestion',gold['question']),
                    source=str((base/'previews'/f'{key}.json').relative_to(ROOT)))
            assert len(entry['candidates'])<=28
            entries.append(entry)
    payload={'llmCalls':0,'selection':'Question category and language fixed before length comparison; regression sample, not new blind holdout',
        'parameters':{'dense':30,'bm25':30,'fusion':28,'top_k':12,'batch_size':8,'synonyms_enabled':False},'cases':entries}
    raw=json.dumps(payload,ensure_ascii=False,indent=2).encode()
    (OUT/'manifest.json').write_bytes(raw);(OUT/'manifest.sha256').write_text(hashlib.sha256(raw).hexdigest())
    print('Frozen',len(entries),'questions; zh/en',sum(e['language']=='zh' for e in entries),sum(e['language']=='en' for e in entries))
if __name__=='__main__':main()
