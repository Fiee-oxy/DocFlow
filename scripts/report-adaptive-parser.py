"""Generate benchmark prose from executed artifacts, never fill unmeasured values."""
import json,shutil
from pathlib import Path
base=Path('evaluation/parser-benchmark');dest=Path('docs/benchmarks/document-parser-benchmark.md');dest.parent.mkdir(parents=True,exist_ok=True)
rows=json.loads((base/'results.json').read_text(encoding='utf-8'))['measurements']
lines=['# 文档解析 Benchmark（实际执行）','','真实 PDF 与合成样本分开列出；这不是解析准确率报告。Python RSS 是10ms抽样的整个进程驻留内存峰值，非单文件增量内存，不与 Java 内存直接比较。端到端包含全页检查，故可能显著高于单纯文本提取耗时。','',
'| 文件 | Synthetic | 大小 MiB | 页数 | 预检 ms | 提取 ms | 总计 ms | 峰值 RSS MiB | 文本页覆盖 | 门禁 |',
'|---|---|---:|---:|---:|---:|---:|---:|---:|---|']
for r in rows:
    lines.append(f"| {r['file_name']} | {r['synthetic']} | {r['file_size']/1024/1024:.2f} | {r['page_count']} | {r['preflight_ms']:.1f} | {r['parse_latency_ms']:.1f} | {r['end_to_end_latency_ms']:.1f} | {r['peak_memory_mb']} | {r['text_coverage']} | {'PASS' if r['quality_gate_result'] else ', '.join(r['issues'])} |")
native=Path('server/target/parser-benchmark/results.json')
if native.exists():
    shutil.copyfile(native,base/'native-results.json')
    lines+=['','## Java 原生解析（全部 Synthetic）','','| 文件 | 总耗时 ms | 原文块数 |','|---|---:|---:|']
    for r in json.loads(native.read_text(encoding='utf-8')):lines.append(f"| {r['file_name']} | {r['end_to_end_latency_ms']} | {r['block_count']} |")
lines+=['','## 真实 Docling 模型 HTTP 验收（Synthetic）','','| 样本 | 请求模式 | 实际模式 | HTTP总耗时 ms | 状态 |','|---|---|---|---:|---|']
for r in json.loads((base/'docling-smoke.json').read_text(encoding='utf-8')):
    lines.append(f"| {r['fixture']} | {r.get('requested_mode','')} | {','.join(r.get('modes',[]))} | {r['elapsed_ms']:.1f} | {r['status']} |")
lines+=['','相同一页文本样本分别运行 AUTO/FAST 与 STANDARD，可以观察冷启动成本差异；单次合成样本不能外推为生产速度提升倍数。modelInitMs 在对应 JSON 中记录显式 initialize_pipeline 时间；模型构造在批内按配置复用，跨任务仍冷启动。',
'','## 解读与边界','',
'- 扫描公报 FAST 无文字，被门禁拒绝；不能因为解析器没有异常就标为完整成功。',
'- 华为年报有疑似图片页需要 OCR，FAST 没有放行全文。茅台文本页覆盖达标不代表表格行列、阅读顺序均正确。',
'- 合成扫描样本已实际通过 FAST→STANDARD→DEEP，识别出160和credits；不能代表真实中英文扫描集的 OCR 准确率。',
'- 双栏/表格/扫描/混合样本在 synthetic/；长DOCX为500段合成文档，压缩后体积较小，不能冒充大型Word基准。',
'- Heading Accuracy、Reading Order Accuracy、Table Structure Accuracy、升级后真实整份扫描文档质量、RAG Hit@K：NOT MEASURED。',
'- Java/Docling峰值内存、模型不同硬件P95及并发吞吐：NOT MEASURED。',
'- 原始数据：evaluation/parser-benchmark/results.json、native-results.json、docling-smoke.json、e2e/results.json、database-test-summary.json、vector-smoke.txt。',
'','## 重现','',
'`python scripts/benchmark-adaptive-parser.py`；`mvn -q -Dtest=NativeParserBenchmarkTest test`（server目录）；`python scripts/smoke-adaptive-docling.py`；`python scripts/report-adaptive-parser.py`。']
dest.write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(dest)
