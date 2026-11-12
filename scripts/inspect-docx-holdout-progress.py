import json
from pathlib import Path
root=Path('/work/evaluation/docx-holdout-20261027')
for p in Path('/proc').iterdir():
    try:
        cmd=p.joinpath('cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
        if 'python' in cmd:print(p.name,cmd[:180])
    except OSError:pass
if (root/'retrieval-results.json').exists():
    r=json.loads((root/'retrieval-results.json').read_text())
    print([(x['document'],x['mode'],x.get('status'),len(x['questions']),sum(q['coverage']['rerank12']['complete'] for q in x['questions'])) for x in r['runs']])
