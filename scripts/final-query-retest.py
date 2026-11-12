"""One corrective 20-client query rerun; preserve the original failed run."""
import concurrent.futures,importlib.util,json,threading
from pathlib import Path
spec=importlib.util.spec_from_file_location('e2e',Path(__file__).with_name('final-e2e.py'))
e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)

def main():
    actors=json.loads((e.OUT/'session.private.json').read_text(encoding='utf-8'))['actors']
    cases=json.loads((e.OUT/'ingestion.json').read_text(encoding='utf-8'))['prepared']
    assert len(cases)==20
    # A new question avoids the answer cache populated by the first run.
    barrier=threading.Barrier(20);results=[];failures=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        futures={pool.submit(e.query,c,actors[c['actor']],barrier,
            'Give both the recovery verification code and the rate limiting algorithm recorded in this document.'):c['i'] for c in cases}
        for future in concurrent.futures.as_completed(futures):
            i=futures[future]
            try:results.append(future.result());print('answered '+str(i),flush=True)
            except Exception as error:failures.append(dict(i=i,error=type(error).__name__+': '+str(error)));print('failed '+str(i),flush=True)
            e.save('query-retest20.json',dict(completed=len(results)==20,concurrency=20,
                note='Same indexed files; new equivalent question; no answer-cache reuse; single inference slot queue timeout 30s',
                results=results,failures=failures))
    print(json.dumps(dict(answered=len(results),failed=len(failures))),flush=True)
if __name__=='__main__':main()
