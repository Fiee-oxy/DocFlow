"""Source-aligned lexical evidence diagnostics; no generative model calls."""
import json, re, unicodedata, hashlib, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

BASE=Path('evaluation/rag-quality'); RUN=BASE/'outputs/pilot-20261013'
def norm(s):
    # PDFBox may encode discretionary line-break hyphens as STX. Normalize
    # this for evidence matching; report its occurrence as a parse warning.
    s=s.replace('\x02','').replace('\u00ad','')
    s=unicodedata.normalize('NFKC',s).casefold().translate(str.maketrans({'“':'"','”':'"','—':'-','–':'-'}))
    s=re.sub(r'(?<=[a-z])-\s*(?=[a-z])','',s)
    return re.sub(r'\s+','',s)
def unit(pages,atoms):return {'pages':pages,'atoms':atoms}
def make_cases():
    cases=[]
    def add(cid,key,lang,q,units,**extra):
        cases.append(dict(case_id=cid,document_key=key,language=lang,question=q,units=units,label_status='SOURCE_ALIGNED_PENDING_HUMAN_REVIEW',**extra))
    f='financebench-3m-2018'; q='qasper-1912.01214'; s='cn-shenzhen-grant-2026'; x='cn-xiaomi-mi2-manual'
    add('en-capex',f,'en','What is the FY2018 capital expenditure amount (in USD millions) for 3M? Give a response to the question by relying on the details shown in the cash flow statement.',[unit([60],['Purchases of property, plant and equipment','1,577','2018','Millions'])])
    add('en-net-ppe',f,'en','Assume that you are a public equities analyst. Answer the following question by primarily using information that is shown in the balance sheet: what is the year end FY2018 net PPNE for 3M? Answer in USD billions.',[unit([58],['Property, plant and equipment','8,738','2018','millions'])],note='Public answer $8.70 has a precision discrepancy; source table is 8,738 million. Evidence only is scored.')
    add('en-baselines',q,'en','which multilingual approaches do they compare with?',[unit([5],['pivoting','multilingual NMT','cross-lingual transfer without pretraining'])])
    add('en-pivot',q,'en','what are the pivot-based baselines?',[unit([2],['Pivot-based Method','pivoting','pivot-synthetic'])])
    add('en-datasets',q,'en','which datasets did they experiment with?',[unit([4],['Europarl','MultiUN','two public'])])
    add('en-languages',q,'en','what language pairs are explored?',[unit([4],['Fr-En-Es','De-En-Fr','Ro-En-De']),unit([5],['Arabic (Ar)','Spanish (Es)','Russian (Ru)'])])
    add('zh-fees',s,'zh','前期费用支持比例最多是多少，实际前期支出相对中方投资总额还有什么限制？',[unit([],['不超过50%','项目中方投资总额的6%'])],paragraphs=[40])
    add('zh-interest',s,'zh','外币贷款贴息利率上限是多少？实际利率低于上限时如何计算？',[unit([],['外币贷款贴息利率不超过3%','实际利率低于3%','按照实际利率计算'])],paragraphs=[45])
    add('zh-grant',s,'zh','单个项目最低支持金额和单家企业年度最高资助分别是多少？',[unit([],['单个项目支持金额不少于5万元','单家企业年度资助不超过1000万元'])],paragraphs=[52])
    add('zh-screenshot',x,'zh','小米手机怎样截屏，需要同时按哪些键？',[unit([6],['菜单键+音量减键','点亮屏幕后','可截取当前屏幕图片'])])
    add('zh-lock-app',x,'zh','怎样让某个后台程序不被一键清除结束？',[unit([29],['向下滑动某个程序','将其锁定','避免“一键清除”终止该程序运行'])])
    add('zh-screen-lock',x,'zh','小米手机支持哪四种屏幕锁定方式？',[unit([30],['人脸','图案','PIN','密码'])])
    return cases
def coverage(rows,case):
    results=[]
    for u in case['units']:
        eligible=[]
        for r in rows:
            pages=r.get('pages',[]) or [p for src in r.get('sources',[]) for p in src.get('pages',[])]
            if u['pages'] and not set(pages).intersection(u['pages']):continue
            eligible.append(r)
        text=norm('\n'.join(r.get('content',r.get('text','')) for r in eligible))
        matches=[norm(a) in text for a in u['atoms']]
        results.append({'complete':all(matches),'matched':sum(matches),'total':len(matches),'missing':[a for a,m in zip(u['atoms'],matches) if not m]})
    return {'complete':all(u['complete'] for u in results),'units':results}
def main():
    import sys
    import pymupdf
    if '--freeze' in sys.argv:
        p=BASE/'pilot-cases.json'
        if p.exists():raise RuntimeError('Cases already frozen; do not overwrite after seeing retrieval')
        p.write_text(json.dumps(make_cases(),ensure_ascii=False,indent=2),encoding='utf-8')
    cases=json.loads((BASE/'pilot-cases.json').read_text(encoding='utf-8'))
    ingestion=json.loads((RUN/'ingestion.json').read_text(encoding='utf-8'))
    results=[]
    for c in cases:
        info=ingestion[c['document_key']]; path=BASE/info['source_path']
        if not path.exists():path=Path(info['source_path'])
        if path.suffix=='.pdf':
            doc=pymupdf.open(path); original=[{'text':p.get_text(),'pages':[i+1]} for i,p in enumerate(doc)]
        else:
            with zipfile.ZipFile(path) as z:root=ET.fromstring(z.read('word/document.xml'))
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            paras=[''.join(p.itertext()) for p in root.findall('.//w:p',ns)]
            original=[{'text':paras[i]} for i in c['paragraphs']]
        stages={'reference':coverage(original,c)}
        for stage,file in [('parsed','parsed.json'),('chunked','rag_chunks.json')]:
            stages[stage]=coverage(json.loads((RUN/'documents'/c['document_key']/file).read_text(encoding='utf-8')),c)
        pp=RUN/'previews'/(c['case_id']+'.json')
        if pp.exists():
            preview=json.loads(pp.read_text(encoding='utf-8')); traces=preview.get('retrieval',[]);trace=traces[0] if traces else {}
            for name in ['dense','bm25','rrf','reranked','topK']:
                stages[name]=coverage(trace.get(name,[]),c)
            for name in ['dense','bm25','rrf','reranked']:
                for k in [5,10,20]:stages[f'{name}@{k}']=coverage(trace.get(name,[])[:k],c)
            model_order=sorted(trace.get('reranked',[]),key=lambda r:r.get('model_rerank_score',float('-inf')),reverse=True)
            stages['model_only@8']=coverage(model_order[:8],c)
            stages['candidate_union']=coverage(trace.get('dense',[])+trace.get('bm25',[]),c)
            stages['context']=coverage(preview.get('evidence',[]),c)
            extra={'strategy':preview.get('queryStrategy'),'latencyMs':preview.get('latencyMs'),'sectionConstrained':trace.get('sectionConstrained'),'selectedSourceCount':preview.get('selectedSourceCount'),'error':preview.get('evaluation_error'),'candidateCounts':{k:len(trace.get(k,[])) for k in ['dense','bm25','rrf','reranked','topK']},'omittedReasons':[r.get('reason') for r in preview.get('omittedEvidence',[])]}
        else:extra={}
        results.append(dict(case_id=c['case_id'],language=c['language'],question=c['question'],stages=stages,**extra))
    output={'status':'PROVISIONAL_LEXICAL_EVIDENCE_DIAGNOSTICS','cases_sha256':hashlib.sha256((BASE/'pilot-cases.json').read_bytes()).hexdigest(),'normalization_note':'Whitespace, case, discretionary hyphens and PDFBox STX line-break artifacts normalized; numbers preserved. This is minimum lexical evidence coverage, not semantic correctness.','results':results}
    (RUN/'stage-results.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    stage_names=['parsed','chunked','dense@5','dense@20','bm25@5','bm25@20','candidate_union','rrf','reranked@5','topK','context']
    summary={'cases':len(results),'languages':{},'case_sha256':output['cases_sha256'],'chat_llm_audit':json.loads((RUN/'no-llm-audit.json').read_text()),'status':output['status']}
    lines=['# 中英文 RAG 首轮诊断基线','',
        '本轮实际执行 4 份文档、12 道题（中英文各 6 题），共 662 个已发布 RAG 内容块。独立评测账号，不调用聊天 LLM。',
        '这是必要原文片段的词面覆盖诊断，不是回答准确率。参考片段在检索前固定，并通过原始 PDF 物理页 / DOCX XML 段落验证；标注尚待人工复核。',
        'PDF 走当前默认 FAST，DOCX 走 Docling；本轮不能评价 Docling PDF 的版面恢复质量。英文 Embedding 仍使用 bge-small-zh-v1.5。',
        '', '| 阶段：全部必要片段覆盖 | 中文 | 英文 | 总体 |','| --- | --- | --- | --- |']
    for stage in stage_names:
        counts=[]
        for lang in ['zh','en']:
            subset=[r for r in results if r['language']==lang]
            count=sum(r['stages'].get(stage,{}).get('complete',False) for r in subset)
            summary['languages'].setdefault(lang,{})[stage]={'complete':count,'total':len(subset)}
            counts.append(count)
        lines.append(f'| {stage} | {counts[0]}/6 | {counts[1]}/6 | {sum(counts)}/12 |')
    lines+=['','## 逐题结果','','| 题目 | 最终必要证据完整 | 首个缺失阶段 |','| --- | --- | --- |']
    sequence=['reference','parsed','chunked','candidate_union','rrf','topK','context']
    for r in results:
        failure=next((s for s in sequence if not r['stages'].get(s,{}).get('complete',False)),'PASS')
        r['first_incomplete_stage']=failure
        lines.append(f"| {r['case_id']}：{r['question']} | {'是' if r['stages'].get('context',{}).get('complete') else '否'} | {failure} |")
    lines+=['','## 主要发现与下一步','',
        '1. 财务题的目标物理页 58 / 60 未进入 Dense 或 BM25 候选。需检查英文分词、缩写/同义表达和向量模型；本轮没有换模型，因此不能把原因确定为某一个模型。',
        '2. pivot-based 题的完整证据在候选和融合池中，但未全部保留到 Top-K；检查精排输入截断、分块边界和排名。',
        '3. datasets 题候选中有参考片段，融合后只保留部分；最终上下文没有数据集名称。先查融合截断，再查预算。',
        '4. language pairs 题遗漏跨页的另一组语言；需要评测相邻块扩展和多证据覆盖，而非只提高单块相似度。',
        '5. 英文题最终均只选择一个来源，预览中存在 OVER_BUDGET 记录。当前按序整块装入 2000 字节上界预算，需对比更细证据选择与预算分配，不能直接把它视作模型实际 Token 数。',
        '6. PDFBox 提取的英文断词存在 STX 控制字符；本轮评分统一断词格式，但生产清洗仍需处理。',
        '', '## 边界与复现','',
        '- 解析/分块 12/12 只表示这些题的指定片段仍可找到，不表示全文表格、阅读顺序和数字列关系均正确。财务表格仍需视觉核验。',
        '- 页码用于严格来源匹配；其他页面的等价证据可能不计入，需要人工复核失败项和补充合法替代证据。',
        '- FinanceBench 净 PP&E 题公开答案精度与原表有差异，保留原始记录；只评证据，不按答案字符串评分。',
        '- 未使用保留文档调参；尚未完成计划中的 12 份文档 / 80 题、无答案题、跨语言题、正式人工标注和盲测。',
        '- 本轮未修改生产检索参数；先冻结基线，再做单因素 A/B。优先英文召回，其次融合/精排保留率，最后上下文预算。',
        '- 原始预览：previews/*.json；逐阶段缺失片段：stage-results.json；运行配置：runtime.json；解析与分块：documents/。',
        '', '```powershell',
        'python scripts/run-rag-quality-pilot.py preview --run-dir evaluation/rag-quality/outputs/pilot-20261013',
        'python scripts/score-rag-quality-pilot.py', '```',
        '', 'preview 默认复用已保存结果；新一轮对比应使用独立运行目录并保留配置版本。不要公开 .session.json（含评测账号凭据）。']
    (RUN/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (RUN/'stage-results.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    (RUN/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps([{'case':r['case_id'],**{k:v['complete'] for k,v in r['stages'].items()}} for r in results],ensure_ascii=False,indent=2))
if __name__=='__main__':main()
