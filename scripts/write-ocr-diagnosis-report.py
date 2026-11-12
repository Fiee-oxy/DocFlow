"""Summarize measured diagnostics without retroactively decomposing old timings."""
import hashlib,json,py_compile,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evaluation/ocr-diagnosis-20261112'
def load(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def main():
    a=load('current.json');b=load('fresh-process.json');c=load('no-table-setup.json');resolution=load('resolution.json');source=load('source-metadata.json')
    rows=[]
    for label,data in [('新进程 A 首次',a),('同一进程 A 复用',a),('新进程 B 再次',b)]:
        index=1 if '复用' in label else 0;run=data['runs'][index]
        assert 'SUCCESS' in run['status']
        commands=run['commands'];recognitions=[r for r in commands if r['kind']=='recognition']
        assert len(recognitions)==5 and len({r['imageSha256'] for r in recognitions})==5
        init=0 if index else data['setupElapsedSeconds']
        rows.append(dict(label=label,setupSeconds=init,conversionSeconds=run['conversionSeconds'],
            measuredTotalSeconds=init+run['conversionSeconds']+run['normalizationSeconds'],
            ocrSeconds=run['doclingTimings']['ocr']['seconds'],
            recognitionSeconds=sum(r['seconds'] for r in recognitions),
            orientationSeconds=sum(r['seconds'] for r in commands if r['kind']=='orientation'),
            ocrRenderSeconds=sum(r['seconds'] for r in run['renders']),
            layoutSeconds=run['doclingTimings']['layout']['seconds'],
            tableExecutionSeconds=run['doclingTimings']['table_structure']['seconds'],blocks=run['blocks'],textSha256=run['textSha256']))
    assert len({r['textSha256'] for r in rows})==1
    assert not a['hardware']['cudaAvailable'] and source['nativeCharacters']==0 and source['pages']==1
    table_off=next(r for r in c['factoryInitializations'] if r['options']=='TableStructureOptions');assert table_off['enabled'] is False
    summary=dict(originalObservedParseSeconds=86.653,originalObservedSubmitToReadySeconds=90.786,
        originalStageBreakdownAvailable=False,source=dict(bytes=source['bytes'],pages=1,nativeCharacters=0,
            sha256=hashlib.sha256((OUT/'source.pdf').read_bytes()).hexdigest()),hardware=a['hardware'],runs=rows,
        resolution=resolution,noDuplicateRecognitionWithinOneRun=True,tableDisabledSetupOnly=True,
        productionPipelineChanged=False,chatLlmCalls=0)
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    table='\n'.join(f"| {r['label']} | {r['setupSeconds']:.2f} | {r['ocrSeconds']:.2f} | {r['recognitionSeconds']:.2f} | {r['orientationSeconds']:.2f} | {r['layoutSeconds']:.2f} | {r['measuredTotalSeconds']:.2f} |" for r in rows)
    factory='\n'.join(f"| {r['options']} | {r['seconds']:.3f} |" for r in a['factoryInitializations'] if r['enabled'] is not False)
    report=f'''# 单页公告 OCR 耗时排查（2026-11-12）

## 结论

这份 PDF 是一页纯图片文档，710,161 字节，原生文字数为 0。生产任务原先的整体解析为 86.653 秒，从上传初始化到 READY 约 90.786 秒。该次运行未开启分项计时，**不能把原来的 86.653 秒重新拆分为精确的 OCR、初始化时间**。以下均为同一原文件在同一解析容器中的后续隔离复测。

## 分阶段实测

单位：秒。准备时间含模块导入、converter 构造和 pipeline 模型初始化；OCR 阶段含区域渲染、图片落盘、方向检测、文字识别与后处理。“识别进程”和“方向检测”包含在 OCR 阶段中，不能重复相加。

| 运行 | 准备 | OCR 阶段 | 识别进程合计 | 方向检测合计 | Layout | 准备＋解析＋归一化 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
{table}

三次均得到 7 个内容块，归一化正文 SHA-256 一致。样本量很小，不是稳定 P95 测试；新进程和文件缓存、网络、首次算子执行等因素都会带来波动。

## 六项逐一排查

| 排查项 | 实测与判断 |
| --- | --- |
| OCR 模型首次加载 | 重的初始化主要是 Docling 的 Layout 和 Table 模型。OCR 适配器构造本轮约 0.368 秒，只包含版本／语言探测；Tesseract 每次加载语言数据的开销计入各次识别进程，不能拿 0.368 秒代表全部 OCR 模型加载。 |
| 实际运行在 CPU | 容器没有 GPU DeviceRequests，CPU 配额 2 核、内存 4 GiB；cudaAvailable=false、PyTorch 线程数 2。Tesseract CLI 和本次 Docling 推理均在 CPU 上。不能据此断言所有耗时都由 CPU 引起。 |
| 同时开启 OCR、Layout、Table | 当前配置全部开启。首轮 Layout 推理约 11.94 秒，复用后 4.71 秒；此页没有表格，表格实际执行不足 0.001 秒，但表格模型初始化仍占 2.55～5.18 秒。 |
| 图片分辨率过高 | 原主图 2268×3472；实际 Docling OCR 使用 scale=3，即约 216 DPI，分为 5 个区域，最大区域 1371×753，并非按原图分辨率对整页重复识别。降分辨率有收益但需核对质量，见下。 |
| 模型重复初始化 | 每个任务由 spawn 创建新进程，模型实例不会跨任务复用。新进程 A/B 均重新初始化 Layout、Table；准备约 25.02／24.55 秒。复用同一 converter 不重复加载。生产原任务 attempts=1，未发现该文件被消息重复执行。 |
| 检测与识别重复执行 | 每次解析有 5 个不同区域，每区一次 OSD 方向检测、一次 TSV 识别，共 10 次相关 CLI 调用。5 个识别输入图像哈希互不相同，没有发现同一区域重复识别；但方向检测累计约 6～7 秒，存在减少逐区域 OSD 的优化空间。 |

首次进程 A 的初始化构成（嵌套阶段，不与总初始化再次相加）：

| 初始化组件 | 秒 |
| --- | ---: |
{factory}

## 分辨率对照

独立的整页 Tesseract 对照，仍使用 chi_sim+eng 和默认识别模式；它与 Docling 的分区域识别不完全相同。

- 216 DPI：1785×2526，识别 {resolution['runs'][0]['recognitionSeconds']:.2f} 秒，输出 {resolution['runs'][0]['textCharacters']} 字符。
- 144 DPI：1190×1684，识别 {resolution['runs'][1]['recognitionSeconds']:.2f} 秒，输出 {resolution['runs'][1]['textCharacters']} 字符。
- 两份去空白识别文本的一致性为 {resolution['textAgreement']*100:.1f}%。**这是文本一致性，不是准确率**；没有人工标注真值，不能认定低 DPI 质量相同。未修改正式默认分辨率。

## 额外发现：缓存模型仍访问外网

新进程初始化会访问 huggingface.co。A 的两个 GET 累计约 2.04 秒；B 的一个 GET 等待约 5.42 秒后失败，随后重试成功。这些时间包含在模型初始化中。记录只保存主机、方法、状态和耗时，不记录 Token 或请求头。固定本地模型目录并验证离线初始化值得优先处理。

## 处理优先级

1. 保留现有隔离能力，设计可回收的常驻解析 Worker，复用模型实例，不能仅把 converter 变量放进每次都会销毁的子进程。
2. 固定本地模型制品并验证离线初始化，避免运行时模型元数据访问导致抖动。
3. 针对方向确定的简单扫描公告，对比整页轻量 OCR、减少逐区域 OSD；复杂排版保留结构解析路径，不能一概关闭 Layout。
4. 表格模型按计划加载；本页虽然没有表格，但不能仅凭“单页”判断其他文档无表格。关闭表格的本轮对照仅测初始化，没有做完整质量验收。
5. 最后再标定 DPI；先验证姓名、日期、数字及阅读顺序，不能只按输出字符数决定质量。

## 范围及复现

本轮仅新增隔离诊断脚本，没有改变生产解析策略、重建索引或调用聊天 LLM。诊断进程串行运行，原文从 MinIO 只读复制。源 PDF 与预览图已加入忽略提交规则。

- `scripts/prepare-ocr-diagnosis.py`：读取指定原文并检查文字层／图片尺寸。
- `scripts/profile-docling-ocr.py`：对已安装 Docling 启用计时并记录初始化、渲染及 Tesseract 命令；仅修改诊断进程中的计时钩子。
- `scripts/run-ocr-profile-cases.py`：新进程、关闭表格初始化、分辨率对照，顺序执行，捕获实际退出码。
- 原始指标：current.json、fresh-process.json、no-table-setup.json、resolution.json。

Docling 的阶段计时存在父子嵌套；报告只选取独立主要阶段，不将 pipeline_total、page_assemble 和 reading_order 等嵌套指标再次累计。
'''
    (OUT/'analysis.md').write_text(report,encoding='utf-8')
    for name in ['prepare-ocr-diagnosis.py','profile-docling-ocr.py','profile-tesseract-resolution.py','run-ocr-profile-cases.py']:
        py_compile.compile(str(ROOT/'scripts'/name),doraise=True)
    print(json.dumps(dict(complete=True,runs=[{key:r[key] for key in ['label','ocrSeconds','recognitionSeconds','orientationSeconds']} for r in rows]),ensure_ascii=False),flush=True)
if __name__=='__main__':main()
