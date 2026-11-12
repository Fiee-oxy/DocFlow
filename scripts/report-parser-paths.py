"""Summarize actual A/B artifacts; distinguish row structure from text presence."""
import argparse,json,re,unicodedata,statistics
from pathlib import Path

def norm(t):return re.sub(r'[^\w\u3400-\u9fff]','',unicodedata.normalize('NFKC',str(t)).casefold())
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def main():
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--require-final',action='store_true');a=p.parse_args();root=a.directory
    rows=load(root/'results/results.json');gold=load(root/'table-gold.json');tables=[]
    manifest=load(root/'manifest.json')
    if a.require_final:
        assert len(rows)==len(manifest['documents'])*3
        assert all(r.get('retrieval_collection_basis')=='FRESH_COLLECTION_PER_RUN' for r in rows)
        replay=load(root/'results/context-replay-summary.json')
        assert replay['retrievalArtifactsReplayed']==sum(len(r['queries']) for r in rows)
        assert load(root/'results/isolated-retrieval-summary.json')['allCollectionsRemoved']
    for row in rows:
        document=next(d for d in manifest['documents'] if d['id']==row['document'])
        row['expected_query_count']=len(document['cases'])
        row['unexecuted_case_ids']=[c['case_id'] for c in document['cases'] if c['case_id'] not in {q['case_id'] for q in row['queries']}]
        for query in row['queries']:
            context=root/'results'/row['document']/row['mode']/(query['case_id']+'-context.json')
            if context.exists():
                packed=load(context);case=next(c for c in document['cases'] if c['case_id']==query['case_id'])
                checks=[]
                for unit in case['units']:
                    text=norm('\n'.join(s['text'] for s in packed['evidence'] if set(s.get('pages',[]))&set(unit['pages'])))
                    checks.extend(norm(a) in text for a in unit['atoms'])
                query['final_context']={'complete':bool(checks) and all(checks),'atoms_found':sum(checks),'atoms_total':len(checks)}
                query['contextReplayMs']=packed.get('contextReplayMs')
                query['javaFusionMs']=packed.get('javaFusionMs')
    (root/'scored-results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    for table in gold['tables']:
        for mode in ['legacy_fast','legacy_docling','auto']:
            path=root/'results'/table['document']/mode/'blocks.json'
            if not path.exists():continue
            blocks=[b for b in load(path) if table['page'] in b.get('pages',[])]
            text=norm('\n'.join(b.get('text','') for b in blocks));structured=[]
            for b in blocks:
                structured.extend(b.get('attributes',{}).get('table',{}).get('rows',[]))
                for line in b.get('text','').splitlines():
                    if '|' in line:structured.append([c.strip() for c in line.strip().strip('|').split('|')])
            details=[]
            for expected in table['rows']:
                matches=[]
                for row in structured:
                    if not row or norm(expected[0]) not in norm(row[0]):continue
                    # Preserve the six expected columns in order in a single row.
                    cells=[norm(c) for c in row[1:]]
                    ok=any(cells[i:i+len(expected)-1]==[norm(c) for c in expected[1:]] for i in range(len(cells)))
                    if ok:matches.append(row)
                details.append({'expected':expected,'all_text_present':all(norm(x) in text for x in expected),'ordered_row_preserved':bool(matches),'matching_rows':matches})
            tables.append({'document':table['document'],'page':table['page'],'mode':mode,'structured_rows_present':bool(structured),
                'evaluation_status':'NO_PAGE_OUTPUT' if not blocks else 'UNSTRUCTURED' if not structured else 'SCORED',
                'selected_rows':len(details),'ordered_rows_correct':sum(x['ordered_row_preserved'] for x in details),'details':details})
    (root/'table-results.json').write_text(json.dumps(tables,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 真实文档解析路径对照评测','',
      '三条路径在相同当前运行环境下执行：旧兼容 FAST/PDFium、旧兼容 Docling、新 AUTO；不是历史二进制版本的独立部署。',
      '不调用聊天 LLM。固定 BGE、Milvus、Dense30/BM25 30、RRF60/28、精排Top12/256Token；嵌入和精排预热，解析任务保持原进程隔离。',
      '计时范围：内部解析服务→逐批同步索引→真实检索确认；不包含上传、RocketMQ、SQL发布和Java最终上下文规划。同步索引会施加背压，因此总耗时不能当作纯解析耗时；parser_wall_less_index_ms也包含预检、传输和探测。',
      '每文档仅运行一次，按文档轮换模式顺序；没有P95或吞吐量结论。现有题目是来源对齐标注，不是独立人工金标。COMPLETE只表示流程结束，不表示每个区域的文字和表格均正确。',
      '本轮复用既有固定文档/题目做路径回归对照，不是全新留出文档的泛化验收；DOCX等原生格式不在本次三路PDF比较范围内。',
      '解析流水线计时使用第一轮真实解析/索引；发现BM25删除后统计残留后，召回质量从冻结的相同解析批次重新索引、查询，每组使用全新集合。旧共享集合召回结果已作废并保留审计文件，未与最终召回结果混用。查询耗时来自新集合重测，isolated_reindex_ms单独记录重建耗时。',
      '', '| 文档 | 模式 | 状态 | 首批解析秒 | 首批可检索秒 | 全文解析及索引秒 | 索引累计秒 | 原文完整题 | 候选完整题 | 精排Top12完整题 |',
      '|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
    if not a.require_final:
        lines.insert(2,'工作中间报告：隔离召回重测与Java预算回放未全部核验前，不作为最终结论。\n')
    def sec(x):return '-' if x is None else f'{x/1000:.2f}'
    for row in rows:
        qs=row['queries'];count=lambda k:sum(q[k]['complete'] for q in qs)
        lines.append(f"| {row['document']} | {row['mode']} | {row['status']} | {sec(row['first_parse_ms'])} | {sec(row['first_searchable_ms'])} | {sec(row.get('pipeline_total_ms'))} | {sec(row['index_ms'])} | {count('parse')}/{row['expected_query_count']} | {count('candidate')}/{row['expected_query_count']} | {count('rerank_top12')}/{row['expected_query_count']} |")
    lines+=['','## 文档清单','','| 文档编号 | 标题 | 语言 | 页数 | MiB |','|---|---|---|---:|---:|']
    for d in manifest['documents']:
        lines.append(f"| {d['id']} | {d['cases'][0].get('title',d['id'])} | {d['language']} | {d['pages']} | {d['bytes']/1048576:.2f} |")
    lines+=['','## 逐文件速度收益与退化','','以下比例仅为本次单次观测，正数表示AUTO更慢，负数表示AUTO更快；不外推生产P95。','',
        '| 文档 | AUTO相对旧Docling总耗时变化 | AUTO相对旧Docling首批可检索变化 |','|---|---:|---:|']
    for d in manifest['documents']:
        pair={r['mode']:r for r in rows if r['document']==d['id'] and r['status']=='COMPLETE'}
        if 'auto' in pair and 'legacy_docling' in pair:
            new,old=pair['auto'],pair['legacy_docling']
            total=100*(new['pipeline_total_ms']/old['pipeline_total_ms']-1)
            first=100*(new['first_searchable_ms']/old['first_searchable_ms']-1) if old['first_searchable_ms'] and new['first_searchable_ms'] else None
            lines.append(f"| {d['id']} | {total:+.1f}% | {first:+.1f}% |" if first is not None else f"| {d['id']} | {total:+.1f}% | 未获得可检索批次 |")
    lines+=['','## 查询耗时（不含解析入库）','','检索/精排来自隔离重测；Java RRF来自实际生产方法回放，若尚未回放则显示参考实现耗时。查询合计来自Python内部检索链路，非聊天HTTP请求总延迟。','',
            '| 模式 | 查询数 | 检索中位秒 | RRF中位毫秒 | 精排中位秒 | 查询合计中位秒 |','|---|---:|---:|---:|---:|---:|']
    for mode in ['legacy_fast','legacy_docling','auto']:
        qs=[q for r in rows if r['mode']==mode for q in r['queries']]
        if qs:
            med=lambda key:statistics.median(q[key] for q in qs)
            fusion=statistics.median(q.get('javaFusionMs') if q.get('javaFusionMs') is not None else q['rrf_ms'] for q in qs)
            lines.append(f"| {mode} | {len(qs)} | {med('search_ms')/1000:.3f} | {fusion:.3f} | {med('rerank_ms')/1000:.3f} | {med('total_query_ms')/1000:.3f} |")
    lines+=['','## 分块粒度诊断','','Token数包括检索标题；小块比例只用于定位碎片化，不直接等于低质量。','',
            '| 文档 | 模式 | 索引块 | 中位Token | 小于80Token块 |','|---|---|---:|---:|---:|']
    for row in rows:
        path=root/'results'/row['document']/row['mode']/'chunks.json'
        if path.exists():
            chunks=load(path)
            if chunks:lines.append(f"| {row['document']} | {row['mode']} | {len(chunks)} | {statistics.median(c['token_count'] for c in chunks):.1f} | {sum(c['token_count']<80 for c in chunks)} |")
    lines+=['','## 分语言统计','','完整题要求指定来源页内的全部必要文字均保留；只命中其中一项不算完整。这个指标评估证据覆盖，不是生成答案准确率。','',
      '| 模式 | 语言 | 完成文档 | 解析完整 | 分块完整 | 候选完整 | RRF完整 | 精排Top12完整 | Java预算后完整 |','|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for mode in ['legacy_fast','legacy_docling','auto']:
        for lang in ['zh','en']:
            selected=[r for r in rows if r['mode']==mode and r['language']==lang];qs=[q for r in selected for q in r['queries']]
            denominator=sum(r['expected_query_count'] for r in selected)
            vals=[f"{sum(q[k]['complete'] for q in qs)}/{denominator}" for k in ['parse','chunk','candidate','rrf','rerank_top12']]
            final=[q for q in qs if 'final_context' in q]
            vals.append(f"{sum(q['final_context']['complete'] for q in final)}/{len(final)}" if final else '未执行')
            lines.append('| '+ ' | '.join([mode,lang,f"{sum(r['status']=='COMPLETE' for r in selected)}/{len(selected)}"]+vals)+' |')
    lines+=['','## 逐题首次丢失阶段','','失败文件中的零覆盖表示未能发布证据，不等于已对全文逐字识别后发现错误。','',
            '| 文档 | 模式 | 问题 | 首次丢失阶段 |','|---|---|---|---|']
    failures=[]
    for row in rows:
        for q in row['queries']:
            first=next((k for k in ['parse','chunk','candidate','rrf','rerank_top12','final_context'] if k in q and not q[k]['complete']),None)
            if first:
                failures.append({'document':row['document'],'mode':row['mode'],'case_id':q['case_id'],'first_loss':first,'status':row['status'],'question':q['question']})
                lines.append(f"| {row['document']} | {row['mode']} | {q['question']} | {first}（{row['status']}） |")
    (root/'first-loss.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2),encoding='utf-8')
    lines+=['','## 表格专项','','逐行对照已视觉核验的两张原始表，要求同一结构化行内六个数值保持正确顺序；未输出结构化行记为未保留结构，不等同数值识别错误。该小样本不代表完整表格结构准确率，表头、合并单元格仍需另评。','',
        '| 文档及页 | 模式 | 存在结构化行 | 正确保留的标注行 |','|---|---|---|---:|']
    for t in tables:
        result='未产出，未评分' if t['evaluation_status']=='NO_PAGE_OUTPUT' else f"{t['ordered_rows_correct']}/{t['selected_rows']}"
        lines.append(f"| {t['document']} p{t['page']} | {t['mode']} | {t['structured_rows_present']} | {result} |")
    if (root/'ocr-gold.json').exists():
        gold_ocr=load(root/'ocr-gold.json');ocr=[]
        lines+=['','## 图片页文字专项','','英文大文件物理页55为图片形式的鉴证材料，原生文字层仅14字符。以下5个短语经原图视觉核验；这是小样本短语保留率，不是OCR字符准确率。','',
                '| 模式 | 保留短语 |','|---|---:|']
        for mode in ['legacy_fast','legacy_docling','auto']:
            path=root/'results'/gold_ocr['document']/mode/'blocks.json'
            if not path.exists():continue
            text=norm('\n'.join(b.get('text','') for b in load(path) if gold_ocr['page'] in b.get('pages',[])))
            detail=[{'phrase':a,'found':norm(a) in text} for a in gold_ocr['phrases']]
            ocr.append({'mode':mode,'details':detail})
            lines.append(f"| {mode} | {sum(d['found'] for d in detail)}/{len(detail)} |")
        (root/'ocr-results.json').write_text(json.dumps(ocr,ensure_ascii=False,indent=2),encoding='utf-8')
    lines+=['','## 验证边界','','Java测试对照实际LocalRetrievalClient.fuse，验证融合结果一致，再使用实际AdaptiveContextBuilder和TokenBudgetManager回放最终证据预算；可用预算固定26761、证据最大20000字节、无历史。该回放不包含Java章节约束/扩展、路由及真实会话历史，不能视为整个聊天API验收。API业务端到端与并发P95未测；失败运行保留部分结果，不计为全文成功。','',
       '原始证据见 results/ 下每个文档、模式的 events.jsonl、blocks.json、chunks.json 和逐题检索结果；表格逐行判定见 table-results.json。']
    (root/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('runs',len(rows),'complete',sum(r['status']=='COMPLETE' for r in rows),'report',root/'report.md')

if __name__=='__main__':main()
