"""Live routed preview checks, no paid model calls."""
import importlib.util,json
from pathlib import Path
spec=importlib.util.spec_from_file_location('e',Path(__file__).with_name('final-e2e.py'));e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
def main():
    actors=json.loads((e.OUT/'accepted-run/session.private.json').read_text(encoding='utf-8'))['actors']
    case=json.loads((e.OUT/'accepted-run/ingestion.json').read_text(encoding='utf-8'))['prepared'][0];token=actors[case['actor']]['token']
    checks=[]
    questions=[('BM25','Find "'+case['code']+'".'),('DENSE','Find semantically similar text about retaining committed work.'),
        ('HYBRID','What is the recovery verification code and rate limiting algorithm?')]
    for expected,question in questions:
        result=e.api('/documents/query/preview',dict(documentIds=[case['doc']],question=question,limit=50),token)
        routing=result['retrieval'][0]['routing'];assert result['llmCalls']==0 and routing['initialRoute']==expected,(expected,routing)
        assert result['evidence'] and all(s['doc_id']==case['doc'] for s in result['evidence'])
        if expected=='BM25':assert not result['retrieval'][0]['dense']
        checks.append(dict(expected=expected,question=question,routing=routing,sources=result['selectedSourceCount'],llmCalls=0))
        e.save('live-route-checks.json',checks);print(expected+' passed',flush=True)
if __name__=='__main__':main()
