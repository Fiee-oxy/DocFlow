"""Preserve strict failures; separate native-text diagnostics are never READY."""
import importlib.util,json,sys
from pathlib import Path
ROOT=Path('/work/evaluation/docx-holdout-20261027')
spec=importlib.util.spec_from_file_location('holdout_runner','/work/scripts/run-docx-stream-ab.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
from pipeline import task_events
def main():
    for doc in json.loads((ROOT/'manifest.json').read_text())['documents']:
        folder=ROOT/doc['key'];run=json.loads((folder/'run.json').read_text())
        source=Path('/work')/doc['path']
        for boundary in (False,True):
            mode='boundary_diagnostic' if boundary else 'stream_diagnostic'
            settings=r.base_settings();settings.update(parse_mode='DOCX_STREAM',docx_strict=False,docx_boundary_aware=boundary)
            result,batches=r.collect(source,settings,folder/f'{mode}-events.jsonl',task_events)
            result.update(diagnostic_only=True,eligible_for_ingestion=False)
            run[mode]=result;r.write_json(folder/'run.json',run)
            print(json.dumps({'document':doc['key'],'mode':mode,'status':result['status'],'batches':result['batch_count']}),flush=True)
            if result['status'] not in {'COMPLETE','PARTIAL_UNSUPPORTED'}:continue
            checks=r.resume_runs(source,settings,folder/mode,batches,task_events,lambda checks:None)
            original=[json.loads(line) for line in (folder/f'{mode}-events.jsonl').read_text().splitlines()]
            expected=[e for e in original if e['type']=='batch'];complete=next(e for e in original if e['type']=='complete')
            for check in checks:
                suffix=[json.loads(line) for line in (folder/mode/check['eventsPath']).read_text().splitlines()]
                check['exact_suffix_equal']=([e for e in suffix if e['type']=='batch']==expected[check['afterBatch']:] and
                                            next((e for e in suffix if e['type']=='complete'),None)==complete)
            run[mode]['resumeChecks']=checks;r.write_json(folder/'run.json',run)

if __name__=="__main__":main()
