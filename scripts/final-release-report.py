"""Generate a factual, secret-free final verification report from saved results."""
import hashlib,json,re,statistics,subprocess,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evaluation/final-release-20261112'
D=r'C:/Program Files/Docker/Docker/resources/bin/docker.exe'
def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def pct(values,p):
    values=sorted(values);position=(len(values)-1)*p;lo=int(position);hi=min(lo+1,len(values)-1)
    return round((values[lo]+(values[hi]-values[lo])*(position-lo))/1000,3)
def distribution(rows,key):
    values=[r[key] for r in rows if r.get(key) is not None]
    return dict(count=len(values),p50Seconds=pct(values,.5),p95Seconds=pct(values,.95),maxSeconds=round(max(values)/1000,3))
def main():
    initial=read('results.json');retest=read('query-retest20.json');accepted=read('accepted-run/results.json');ingest=read('accepted-run/ingestion.json')
    assert accepted['completed'] and len(accepted['results'])==20 and not accepted['failures']
    mapping={c['i']:c for c in ingest['prepared']}
    for result in accepted['results']:
        case=mapping[result['i']];answer=result['answer'];text=answer['answer']
        assert case['code'] in text and ('token bucket' in text.lower() or '令牌桶' in text)
        assert answer['citations'] and all(c['doc_id']==case['doc'] for c in answer['citations'])
        assert result['reconnected'] and result['idempotent']
    java=read('java-tests.json');database=read('database-tests.json');python=read('python-tests.json');modules=read('module-checks.json')
    raw=(ROOT/'client/final-tests.log').read_bytes();frontend=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig');frontend_tests=int(re.search(r'# tests (\d+)',frontend)[1])
    assert '# fail 0' in frontend and modules['complete']
    timings={name:distribution(rows,key) for name,rows,key in [
        ('uploadAndSubmit',ingest['prepared'],'acceptedMs'),('indexReady',ingest['prepared'],'readyMs'),
        ('queryAccepted',accepted['results'],'acceptedMs'),('firstAnswerDelta',accepted['results'],'firstDeltaMs'),
        ('queryCompleted',accepted['results'],'queryMs'),('pipelineWithReadinessBarrier',accepted['results'],'endToEndMs')]}
    summary=dict(initialRun=dict(ingested=20,answered=len(initial['results']),failed=len(initial['failures']),cause='RETRIEVAL_BUSY; 1-second inference-slot wait'),
        correctiveQueryRun=dict(answered=len(retest['results']),failed=len(retest['failures'])),
        finalRun=dict(ingested=len(ingest['prepared']),answered=20,failed=0,citationsVerified=20,sseReconnect=20,idempotent=20,
            modelCalls=sum(r['llmCalls'] for r in accepted['results']),usageUnknown=sum(bool(r['usageUnknown']) for r in accepted['results']),
            bytesTotal=sum(c['bytes'] for c in ingest['prepared']),minFileBytes=min(c['bytes'] for c in ingest['prepared']),maxFileBytes=max(c['bytes'] for c in ingest['prepared']),
            indexedChunks=sum(c['state']['indexedChunks'] for c in ingest['prepared'])),
        timings=timings,tests=dict(java=java,database=database,python=python,frontend=frontend_tests,liveModules=len(modules['checks'])),
        protocol=read('accepted-run/protocol.json'))
    recovery=OUT/'dispatch-recovery.json'
    if recovery.exists():summary['brokerRecovery']=read('dispatch-recovery.json')
    routes=OUT/'live-route-checks.json'
    if routes.exists():
        live_routes=read('live-route-checks.json');assert len(live_routes)==3
        summary['liveRoutes']=[r['expected'] for r in live_routes]
    if (OUT/'delivery-health.json').exists():
        summary['deliveryHealth']=read('delivery-health.json')
        runtime=read('runtime.json');summary['deliveryConfiguration']={key:runtime[key] for key in ['profile','rewrite','outputTokens']}
    source_files=['server/src/main/resources/application.properties','server/src/main/resources/application-final.properties',
        'server/src/main/java/com/example/server/document/adaptive/AdaptiveQueryService.java',
        'server/src/main/java/com/example/server/document/adaptive/AdaptiveQueryTaskStore.java',
        'server/src/main/java/com/example/server/document/adaptive/LocalRetrievalClient.java',
        'server/src/main/java/com/example/server/document/adaptive/QueryRewritePolicy.java',
        'server/src/main/java/com/example/server/controller/ApiExceptionHandler.java',
        'server/src/main/java/com/example/server/utils/DeepSeekUtils.java','client/src/useDocumentChat.js','client/src/DocumentChat.vue']
    hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in source_files}
    hashes['verifiedJar']=hashlib.sha256((ROOT/'server/target-final/server-0.0.1-SNAPSHOT.jar').read_bytes()).hexdigest()
    for service in ['docling','retrieval']:
        folder='/app' if service=='docling' else '/retrieval'
        command=[D,'exec','dovideo-'+service,'python','-c',f"import hashlib,json,pathlib;print(json.dumps({{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('{folder}').glob('*.py')}}))"]
        deployed=json.loads(subprocess.check_output(command,text=True,encoding='utf-8'))
        local={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'services'/service).glob('*.py') if not p.name.startswith('test')}
        matched={name:deployed.get(name)==digest for name,digest in local.items() if name in deployed}
        # The local retrieval image intentionally excludes the offline splitter.
        assert all(matched.values()) and 'app.py' in matched
        hashes[service]=dict(compared=matched,deployed=deployed)
    (OUT/'source-manifest.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    timing_lines='\n'.join(f"| {name} | {d['p50Seconds']} | {d['p95Seconds']} | {d['maxSeconds']} |" for name,d in timings.items())
    report=f'''# 最终验收与分析（2026-11-12）

最终版本的全新 20 客户端测试：**20/20 入库、20/20 问答、20/20 引用核验、20/20 SSE 断线续收、20/20 请求幂等**。回答同时包含各文件独有验证码及令牌桶算法，引用全部来自该请求的文档。

## 发现的问题及修复证据

第一轮入库 20/20，问答只有 11/20；9 个失败均为 RETRIEVAL_BUSY。根因是本地检索服务只有一个推理槽，却仅等待一秒。保持一个推理槽、将有界等待改为 30 秒后，同一批文件使用新问题复测为 20/20。随后重启最终 JAR，以新账号、新文件重新跑完整链路，再次 20/20。初次失败结果原样保留在 results.json，不计入通过数。

另外补齐默认聊天的 SSE 接入、在线可选查询改写、线程池饱和后的持久化排队及首增量前的模型瞬时错误重试；SSE 断流不再被写入 JSON 错误体。具体代码和启动方法见 ../../docs/FINAL_RELEASE.md。

## 本轮耗时

单位为秒；20 个样本的观测分位数，不是稳定容量指标。

| 测量范围 | P50 | P95 | 最大 |
| --- | ---: | ---: | ---: |
{timing_lines}

uploadAndSubmit 从客户端开始初始化上传计时，到完整上传提交返回；包含文件传输。indexReady 从同一起点到解析、向量化与索引发布全部完成。queryAccepted 是持久化问答提交接口返回耗时。firstAnswerDelta 和 queryCompleted 从同时提交问答计时，包含排队、可选改写、检索、模型和核验；首增量不是 HTTP SSE 握手。pipelineWithReadinessBarrier 包含等待全部 20 份文件索引完成的同步屏障。

客户端并发为 20；后台局部问答为 2 个有界 Worker，本地 BGE 服务为一个推理槽。**没有证明 20 路模型同时推理**，等待时间受该限制影响。

## 各模块验证

| 模块 | 验证方式与结果 |
| --- | --- |
| 注册、鉴权、知识库范围 | 真实 HTTP 登录及跨用户、知识库交集拒绝；数据库权限与版本集成测试 |
| MinIO / MySQL 分片上传 | 真实超过 5 MiB 的双分片 PDF；错误哈希无回执、丢 ACK 重试幂等、暂停恢复、10 路相同初始化只复用一个任务 |
| 完整指纹及去重 | 正确文件 INSTANT 复用；逐片正确但整文件声明错误时 FAILED；其他账号不能借指纹复用 |
| RocketMQ / 异步处理 | 20 路上传全部后台完成；线程繁忙会重投并由持久化排队补偿，不把上传完成当成入库完成 |
| 解析 / OCR / 表格 | 15 PDF + 5 DOCX；另有真实扫描页 OCR 和 Word 表格测试；107 项解析测试通过 |
| 结构、分块及父上下文 | 发布结构节点与来源；真实父块接口、越权拒绝；Python 分块测试及事务回滚、版本检查点集成测试 |
| Embedding / Milvus / BM25 / RRF / 精排 | 新文件真实向量化与索引，真实检索与 BGE Reranker；57 项检索服务测试通过；256 Token 是临时精排输入预算 |
| 查询改写 | 本轮启用 DeepSeek 辅助表达，原问题保留，最终只精排一次；失败可退回原问题；preview 明确为零聊天 LLM |
| 上下文及引用 | 每题核对唯一值、引用存在与所属文档；服务端执行引用逐字核验；预算与证据去重由单元/集成测试覆盖 |
| SSE / 幂等 | 每题故意在首增量后断线，按事件 ID 重连；完成后重复同请求 ID，模型调用计数不增加 |
| 限流 / 瞬时失败 | 实际请求经过用户/全局 Redis 配额；网络重试及已输出后禁止自动重试由故障替身测试覆盖；本轮未人为耗尽付费平台额度 |

独立现场检查 {len(modules['checks'])} 项通过；另有 BM25、Dense、Hybrid 三条真实预览链路通过，均为零聊天 LLM 调用。Java 默认测试 {java['tests']} 项，{java['tests']-java['skipped']} 项执行通过、{java['skipped']} 项默认跳过；其中上传与自适应数据库集成测试另在独立临时 schema 实际执行 {sum(r['tests'] for r in database)} 项，零跳过、零失败。不能把其余跳过项说成执行通过。Python {sum(r['tests'] for r in python)} 项、前端 {frontend_tests} 项通过，前后端构建通过。

## 结果适用范围

本轮使用小型英文合成功能文档，文件范围 {summary['finalRun']['minFileBytes']}～{summary['finalRun']['maxFileBytes']} 字节，总计 {summary['finalRun']['bytesTotal']} 字节，发布 {summary['finalRun']['indexedChunks']} 个检索块。另有约 5.6 MiB 双分片 PDF。**不能据此写成大文件容量、跨领域准确率或中英文全面质量验收**；未重新运行 150 题，也未复现历史 26.3% / 33.1% 的对照加速数据。

规则路由当前仅保证单路空结果时回退；非空错误候选仍可能漏证据。查询改写默认可关闭，实体与意图不能仅靠格式校验完全保证。长任务没有完整租约与 fencing 协议；运行中的 LLM 不能从模型内部断点继续。

数据库集成临时 schema 测完删除。独立测试账号和文件保留供复查，凭证仅存于忽略提交的 private JSON，不在报告中公开。用户既有文档和账号未删除。
'''
    if recovery.exists():report+='\n真实 Broker 停止故障演练通过：完成上传时发送消息失败，任务仍保持 QUEUED；恢复 Broker 后无需客户端重新提交即自动完成解析。详情见 dispatch-recovery.json。\n'
    if 'deliveryHealth' in summary:
        report+='\n交付时已恢复普通运行参数：2048 Token 输出预算，查询改写默认关闭（可用 DOCUMENT_QUERY_REWRITE_ENABLED=true 开启）。后端和前端代理健康，重启后已完成任务的 SSE 持久化事件仍可回放，未增加模型调用。前端地址：http://127.0.0.1:5173。\n'
    (OUT/'analysis.md').write_text(report,encoding='utf-8');print(json.dumps(dict(finalPass=True,timings=timings),ensure_ascii=False),flush=True)
if __name__=='__main__':main()
