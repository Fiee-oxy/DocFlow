"""Temporary evaluation-only parser endpoint; no production configuration mutation."""
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pipeline import task_events, encode_event

app = FastAPI()
ROOT = Path('/tmp/parser-ab')
MANIFEST = json.loads((ROOT/'manifest.json').read_text())

@app.get('/health')
def health(): return {'ready': True}

@app.post('/run/{document}/{mode}')
def run(document: str, mode: str):
    if document not in {d['id'] for d in MANIFEST['documents']} or mode not in ['legacy_fast','legacy_docling','auto']:
        raise HTTPException(400,'Unknown evaluation scope')
    settings = dict(MANIFEST['settings'])
    settings.update(adaptive_enabled=mode=='auto', adaptive_mode='AUTO',
                    parse_mode='FAST' if mode=='legacy_fast' else 'AUTO' if mode=='auto' else 'DOCLING')
    source=ROOT/(document+'.pdf')
    def events():
        for event in task_events(source,settings): yield encode_event(event)
    return StreamingResponse(events(),media_type='application/x-ndjson')
