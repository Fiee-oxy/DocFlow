"""Tokenizer-only protocol checks, excluded from real-question quality scores."""
import importlib.metadata,json
from pathlib import Path
from transformers import AutoTokenizer,AutoConfig
ROOT=Path('/work/evaluation/rerank-length-ab-20261029')

def main():
    name='BAAI/bge-reranker-base';t=AutoTokenizer.from_pretrained(name);config=AutoConfig.from_pretrained(name)
    ceiling=min(t.model_max_length,config.max_position_embeddings-config.pad_token_id-1)
    cases=[('en-short','What is the limit?','The limit is stated here.'),
           ('en-long','What is the limit?','system throughput and timeout measurements. '*200),
           ('zh-short','如何处理超时？','检查连接与服务状态。'),
           ('zh-long','如何处理超时？','系统吞吐量与连接超时的测试记录。'*200)]
    rows=[]
    for key,q,text in cases:
        encoded=t(q,text,truncation='only_second',max_length=ceiling,stride=32,return_overflowing_tokens=True)
        rebuilt=[]
        for i,ids in enumerate(encoded['input_ids']):
            sequence=encoded.sequence_ids(i)
            assert [v for v,s in zip(ids,sequence) if s==0]==t(q,add_special_tokens=False)['input_ids']
            piece=[v for v,s in zip(ids,sequence) if s==1]
            rebuilt.extend(piece if i==0 else piece[32:])
            assert len(ids)<=ceiling
        assert rebuilt==t(text,add_special_tokens=False)['input_ids']
        rows.append({'case':key,'windows':len(encoded['input_ids']),'exact_token_reconstruction':True,'question_unchanged':True})
    report={'llmCalls':0,'inferenceCalls':0,'checks':rows,'packages':{p:importlib.metadata.version(p) for p in ('torch','transformers','tokenizers')}}
    (ROOT/'protocol-checks.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(report))
if __name__=='__main__':main()
