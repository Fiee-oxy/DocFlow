"""Summarize artifacts; never replace compatibility failures with diagnostics."""
import json,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'evaluation/docx-holdout-20261027'

def main():
    manifest=json.loads((ROOT/'manifest.json').read_text(encoding='utf8'))
    quality=json.loads((ROOT/'results.json').read_text(encoding='utf8'))
    retrieval=json.loads((ROOT/'retrieval-results.json').read_text(encoding='utf8'))
    gold=json.loads((ROOT/'questions.json').read_text(encoding='utf8'))
    assert retrieval['coverage_revision']=='source-row-cells-and-provenance-v3'
    assert all(row.get('status') in {'COMPLETE','NOT_INGESTIBLE'} for row in retrieval['runs'])
    rows=['# 独立真实 Word 留出测试（2026-10-27 开始，10-28 续跑）','',
      '**结论：保持 DOCLING 默认；DOCX_STREAM 及边界实验继续 opt-in。** 两份独立 Word 均暴露新解析器的富对象兼容性缺口，不能用正文诊断的召回结果替代严格完成。','',
      '## 数据与固定条件','',
      '| 文档 | 实际文件大小 | document.xml | 原文规模 | 来源 |','|---|---:|---:|---|---|']
    for doc in manifest['documents']:
        d=next(d for d in quality['documents'] if d['key']==doc['key']);f=d['features']
        rows.append(f"| {doc['title']} | {doc['bytes']/1e6:.2f} MB | {doc['document_xml_bytes']/1e6:.2f} MB | {f['nonempty_paragraphs']} 段、{f['tables']} 表、{f['table_cells']} 单元格 | [官方页面]({doc['source_page']}) |")
    rows+=['','两份文件的 SHA-256 与下载失败记录见 manifest.json、download-attempts.json；均未用于此前开发。英文文件约 16.26 MB，以图片为主，不将其称为 20 MB 超长正文；中文文件约 0.54 MB，但包含 26 张表、548 行及 3.46 MB 正文 XML。未用重复拼接或填充制造大文件。','',
      '中英文各 12 道自然问题，包含数字、列表、表格及多段证据。问题和必要原文在解析/检索前固定，questions.sha256 可核对。未根据本轮结果更改解析模型、召回参数或问题。',
      '固定 Dense/BM25 各 Top-30，RRF k=60/池 28，保留每路前 5 个单路命中，BGE Reranker Top-12、256 Token 临时输入。结构分块目标 350、硬上限 480 Token，仍用原有 HybridChunker。',
      '调用生产解析及检索组件，使用本地 BGE 与真实 Milvus；每方案独立临时集合。没有调用聊天 LLM，LLM calls=0。未覆盖上传/MQ/SQL/Java 章节规划或最终上下文预算，不称为完整端到端回答准确率。','',
      '## 严格兼容性与耗时','',
      '| 文档 | 路径 | 状态 | 首批事件/总耗时（秒） |','|---|---|---|---:|']
    for doc in manifest['documents']:
        run=json.loads((ROOT/doc['key']/'run.json').read_text(encoding='utf8'))
        for mode in ('docling','stream','boundary'):
            r=run[mode];first='无' if r['first_batch_ms'] is None else f"{r['first_batch_ms']/1000:.2f}"
            rows.append(f"| {doc['key']} | {mode} | {r['status']} | {first} / {r['total_ms']/1000:.2f} |")
    rows+=['','Docling 两份转换均结束；新路径两份均严格失败，故不作“新方案更快”的完成性能比较。中文报告含无法映射为图片或文本的绘图对象；英文文件有 EMF，当前 Pillow/OCR 路径不支持。流式路径提前产出的局部批次不等于整份文档成功或可标记 READY。旧 Docling 转换结束也不证明所有图形语义已恢复。',
      '上述是一次串行解析测量，含子进程与事件落盘，不是线上 SLA。非严格诊断曾与评测并行，其时间不用于速度优劣结论。新方案严格失败，因此没有提供具有业务可用含义的“首批可检索时间”。','',
      '## 原文与结构质量','',
      '| 文档 | 路径 | 正文保留 | 单块保留完整段落 | 显式章节路径匹配 | 表格文本及行列跨度 |','|---|---|---:|---:|---:|---:|']
    for d in quality['documents']:
        for mode in ('docling','stream_diagnostic','boundary_diagnostic'):
            q=d['modes'][mode]['quality']
            values=[]
            for field in ('paragraph_text_retained','paragraph_in_single_block','explicit_section_path','table_cells_text_and_spans'):
                v=q[field];values.append(f"{v['matched']}/{v['total']}" if v['total'] else '不适用')
            rows.append('| '+d['key']+' | '+mode+' | '+' | '.join(values)+' |')
    rows+=['','diagnostic 表示保留不支持对象提示后继续提取正文，**不能完整入库**。正文与结构评分以独立 OOXML 为依据，只归一化空白；未进行 Word 渲染或人工图片 OCR 正确率标注。原 Docling 的 1 个表格单元格差异涉及合并单元格，不能笼统归因为所有表格解析失败。','',
      '## 必要证据完整率（不是模型回答准确率）','',
      '| 文档 | 路径 | 解析 | 分块 | 候选并集 | RRF 池 | 精排 Top-12 |','|---|---|---:|---:|---:|---:|---:|']
    for row in retrieval['runs']:
        if row.get('status')!='COMPLETE':continue
        n=len(row['questions']);values=[f"{sum(q['coverage'][s]['complete'] for q in row['questions'])}/{n}" for s in ('parse','chunk','union','fusion','rerank12')]
        rows.append('| '+row['document']+' | '+row['mode']+' | '+' | '.join(values)+' |')
    rows+=['','每道题要求所有必要证据齐全；允许证据来自多个 chunk，不要求沿用同一 chunk ID。表格按原始行/单元格和来源位置核对，避免 Markdown 加粗、表格序列化标点引发假漏失；该评分是可审计的自动证据匹配，不代表人工语义评测。早期纯字符串评分保留在 coverage_v1 中，不再作为结论。','',
      '## 漏失定位与检索耗时','']
    for row in retrieval['runs']:
        if row.get('status')!='COMPLETE':continue
        times=[q['timing_ms'] for q in row['questions']]
        rows.append(f"- {row['document']} / {row['mode']}：{row['chunks']} chunks；检索、RRF、精排中位数分别为 "+'/'.join(f"{statistics.median(t[s] for t in times):.1f}" for s in ('search','rrf','rerank'))+' ms。')
        for q in row['questions']:
            if q['coverage']['rerank12']['complete']:continue
            first=next(s for s in ('parse','chunk','union','fusion','rerank12') if not q['coverage'][s]['complete'])
            rows.append(f"  - {q['id']}「{q['question']}」：首次未完整保留阶段 `{first}`。原文与各阶段候选详见 questions.json / retrieval-results.json。")
    rows+=['','CPU 限制为 2 核、容器内存 6 GB；模型预热另记。解析诊断与前一轮部分基线请求并行，关机后又续跑，因此这些延迟用于排查阶段成本，不用于作公平速度排名。召回参数与题目保持相同。','',
      '## 批次边界实现及结论','',
      '- 默认关闭。软阈值达到后，未发布批次最多额外容纳 8 个正文节点（可配 1–16），额外字符软阈值 1200；单个完整节点仍受硬上限保护。',
      '- 标题与后续正文、同章节短段落/列表可同批；完整段落不直接拼成新原文。组成检索 chunk 时仍检查章节及 Token 预算。',
      '- 表格必须同章节、同源部件、正文相邻、同表编号且显式标为续表，列数和显式重复表头一致；否则不合并。合并时保留原始 source_fragments、节点/表标识，移除重复表头并调整行偏移。',
      '- 已发布批次不回写；规则版本和参数进入检查点契约，防止恢复时混用规则。','']
    for doc in manifest['documents']:
        folder=ROOT/doc['key'];run=json.loads((folder/'run.json').read_text(encoding='utf8'))
        events=[json.loads(s) for s in (folder/'boundary_diagnostic-events.jsonl').read_text(encoding='utf8').splitlines()]
        b=[e['docxBoundary'] for e in events if 'docxBoundary' in e]
        checks=[c for mode in ('stream_diagnostic','boundary_diagnostic') for c in run[mode]['resumeChecks']]
        rows.append(f"- {doc['key']}：{len(b)} 批，额外节点合计 {sum(v['extraNodes'] for v in b)}，实际续表合并 {sum(v['tableMerges'] for v in b)}；首/中/末检查点精确后缀一致 {sum(c['exact_suffix_equal'] for c in checks)}/{len(checks)}。")
    rows+=['','两份真实文档没有满足严格续表合并条件的实例，因此不能宣称真实续表合并已得到收益验证；正例、误合并、表头/章节变化和资源上限通过独立构造用例检查。真实文档验证的是保守拒绝及批次打包，未证明所有跨页表格都能合并。',
      '**取舍：** 结构与原文保留有所改善不等于整体可替换；即使正文诊断召回更好，兼容性失败仍是默认切换的阻断项。先保留旧默认和实验开关；这次不继续扩大解析器改造或围绕留出题调参。','',
      '## 复现及产物','',
      '`fetch-docx-holdout.py` → `freeze-docx-holdout-questions.py`（gold 仅可首次生成）→ `run-docx-stream-ab.py --modes docling stream boundary --no-resume` → 单独 `run-docx-holdout-diagnostics.py` → `evaluate-docx-holdout-retrieval.py` / `--diagnostics-only` → `--rescore-only` → `score-docx-stream-ab.py` / `report-docx-holdout.py`。解析与检索脚本的容器目录为 `/work`、`/app`、`/retrieval`。','',
      '首次临时检索镜像缺 Tesseract 的前置失败另存 prerequisite-run，安装 eng/chi_sim 后才进行正式严格兼容性测试；不是将依赖缺失算作文件解析质量。关机前后结果分别保留，不覆盖原始证据。',
      '详细数据：manifest.json、questions.json、各文档 *-events.jsonl、*-chunks.json、run.json、results.json、retrieval-results.json。临时集合由评测 finally 删除，不写入用户知识库；默认配置未修改。']
    (ROOT/'analysis.md').write_text('\n'.join(rows)+'\n',encoding='utf8')
    print(ROOT/'analysis.md')
if __name__=='__main__':main()
