"""Offline audit on frozen paraphrases. This is NOT a retrieval quality test."""
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services/retrieval'))
from query_expansion import QueryExpander

def main():
    expander=QueryExpander(ROOT/'services/retrieval/synonyms.json',enabled=True)
    frozen=json.loads((ROOT/'evaluation/chunk-expanded-20261019/evaluated-queries.json').read_text(encoding='utf8'))
    questions={q['case_id']:q['question'] for q in frozen if q['case_id'].endswith('-paraphrase')}
    rows=[]
    for key,question in questions.items():
        start=time.perf_counter();plan=expander.expand(question)
        rows.append({'case_id':key,'plan':plan,'expansion_ms':(time.perf_counter()-start)*1000})
        assert plan['expanded_query'].startswith(question)
        assert len(plan['terms'])<=6
    output=ROOT/'evaluation/query-expansion-20261028';output.mkdir(parents=True,exist_ok=True)
    report={'llmCalls':0,'scope':'Dictionary trigger and preservation audit only; no search/index/model calls',
        'quality_ab_completed':False,'unique_paraphrases':len(rows),
        'dictionary_triggered':sum(bool(r['plan']['terms']) for r in rows),'queries':rows}
    (output/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='queries'},ensure_ascii=True))
if __name__=='__main__':main()
