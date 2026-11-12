"""Validate saved experiment records; never performs inference or network calls."""
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'evaluation/three-experiments-20261105'

def read(name):
    return json.loads((OUT / name).read_text(encoding='utf-8'))

a, b, c = [read(name)['records'] for name in
           ('A-stages.json', 'B-recall-only.json', 'C-rerank-only.json')]
assert len(a) == 6 and all(r['result']['ok'] for r in a)
assert len(b) == 320 and all(r['result']['ok'] for r in b)
assert set(Counter((r['id'], r['mode']) for r in b).values()) == {5}
assert all(r['result']['data']['routing']['rerankCandidates'] == 0 for r in b)
fixture = read('c-fixture.json')
by_id = {f['case']['case_id']: f for f in fixture}
assert len(c) == 60 and Counter(r['count'] for r in c) == dict.fromkeys((5,8,12,16,24),12)
for r in c:
    assert r['inputIds'] == [h['chunk_id'] for h in by_id[r['id']]['candidates'][:r['count']]]
fixture_hash = hashlib.sha256((OUT/'c-fixture.json').read_bytes()).hexdigest()
assert fixture_hash == read('C-rerank-only.json')['fixtureSha256']
assert 'Ran 34 tests' in (OUT/'python-tests.log').read_text(encoding='utf-8')
files = ['services/retrieval/app.py', 'services/retrieval/test_stage_timing.py',
         'evaluation/routing/benchmark.py', 'evaluation/routing/three_http.py',
         'evaluation/routing/three_report.py', 'scripts/experiment-c-reranker.py',
         'scripts/prepare-three-experiments.py', 'scripts/serve-three-experiments.py',
         'server/src/main/java/com/example/server/document/adaptive/ParentContextExpander.java',
         'server/src/main/java/com/example/server/document/adaptive/AdaptiveQueryService.java']
result = dict(status='PASS', A_success=6, B_success=320, B_rerank_calls=0,
              C_real_inference_runs=60, C_unique_questions=12, fixtureSha256=fixture_hash,
              pythonTests=34, sourceHashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files})
(OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS: A=6, B=320 without reranking, C=60; fixture prefixes and hashes verified')
