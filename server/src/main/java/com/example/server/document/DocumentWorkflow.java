package com.example.server.document;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.redisson.api.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import java.nio.file.Files;
import java.util.*;
import java.util.concurrent.TimeUnit;

/** Parse and persist source chunks for retrieval. No model calls during ingestion. */
@Service
public class DocumentWorkflow {
    private static final Logger log = LoggerFactory.getLogger(DocumentWorkflow.class);
    private final DocumentRepository repository;
    private final DocumentUploadService uploads;
    private final DoclingClient parser;
    private final DocumentChunker chunker;
    private final RedissonClient redisson;
    private com.example.server.document.adaptive.AdaptiveDocumentRepository adaptive;
    @org.springframework.beans.factory.annotation.Autowired
    public void adaptiveIngestion(com.example.server.document.adaptive.AdaptiveDocumentRepository adaptive) { this.adaptive=adaptive; }
    @Value("${document.parser-workers:1}") private int parserWorkers = 1;
    @Value("${document.fast-parser-workers:4}") private int fastParserWorkers = 4;
    @Value("${document.pdf-parser:FAST}") private String pdfParser = "FAST";
    @Value("${document.docx-parser:DOCLING}") private String docxParser = "DOCLING";
    @Value("${document.docling-document-ids:}") private String doclingDocumentIds = "";
    public DocumentWorkflow(DocumentRepository repository, DocumentUploadService uploads, DoclingClient parser,
                            DocumentChunker chunker, RedissonClient redisson) {
        this.repository = repository; this.uploads = uploads; this.parser = parser;
        this.chunker = chunker; this.redisson = redisson;
    }
    public void process(String id) {
        RLock lock = redisson.getLock(DocumentUploadService.lockKey(id));
        if (!lock.tryLock()) return;
        RLock gate = null;
        try {
            DocumentRecord doc = repository.get(id);
            if (List.of("COMPLETED", "FAILED", "UPLOADING", "CANCELLED").contains(doc.status())) return;
            String resumeState = adaptive == null ? null : adaptive.resumeState(id, doc.parseBatches() - 1);
            String mode = parserMode(doc, resumeState);
            if (!Set.of("AUTO", "HIGH_ACCURACY", "FAST", "DOCLING", "DOCX_STREAM").contains(mode)) throw new IllegalArgumentException("未知的解析模式");
            if (!doc.parseComplete()) {
                int slots = "FAST".equals(mode) ? fastParserWorkers : parserWorkers;
                for (int slot = 0; slot < Math.max(1, Math.min(16, slots)); slot++) {
                    var candidate = redisson.getLock("lock:document:parser:" + mode + ":" + slot);
                    if (candidate.tryLock()) { gate = candidate; break; }
                }
                if (gate == null) return;
            }
            final RLock parserGate = gate;
            repository.begin(id);
            if (doc.parseComplete()) {
                if(adaptive!=null) {
                    try { adaptive.bootstrap(id,doc.userId()); adaptive.finish(id); }
                    catch(Exception e){throw new IllegalStateException("文档策略初始化失败",e);}
                }
                repository.parsed(id); return;
            }
            try {
                if(adaptive!=null&&doc.totalBlocks()>0&&adaptive.state(id)==null)adaptive.bootstrap(id,doc.userId());
                repository.stage(id, "MERGING");
                var file = uploads.materialize(doc);
                try {
                    repository.stage(id, "PARSING");
                    var saved = repository.blocks(id);
                    int[] cursor = {doc.parsedPages(), doc.parseBatches(), doc.totalBlocks()};
                    long started = System.nanoTime();
                    long[] chars = {saved.stream().mapToLong(b -> b.text().length()).sum()};
                    var mapper = new ObjectMapper();
                    boolean nodeStream = "DOCX_STREAM".equals(mode);
                    var checkpoint = resumeState == null ? null : mapper.readTree(resumeState);
                    int[] nodeCursor = {nodeStream && checkpoint != null ? requiredNode(checkpoint, "nextBodyNode") : 0};
                    if(nodeStream && (doc.parsedPages()!=0 || doc.totalPages()!=0 ||
                            (checkpoint != null && requiredNode(checkpoint,"nextBatch") != cursor[1])))
                        throw new IllegalArgumentException("DOCX 节点检查点与已提交批次不一致");
                    Set<String> warnings = new LinkedHashSet<>();
                    if (doc.warningsJson() != null) for (var warning : mapper.readTree(doc.warningsJson())) warnings.add(warning.asText());
                    if(nodeStream && checkpoint!=null)requireCompleteDocx(id,checkpoint,warnings,mapper);
                    String heading = saved.isEmpty() ? "" : saved.getLast().section();
                    DoclingClient.EventConsumer consumer = event -> {
                        requireLock(lock); requireLock(parserGate);
                        if("CANCELLED".equals(repository.get(id).status()))throw new java.util.concurrent.CancellationException("文档解析已取消");
                        if(nodeStream && Set.of("batch","complete").contains(event.path("type").asText()))
                            requireCompleteDocx(id,event,warnings,mapper);
                        switch (event.path("type").asText()) {
                            case "metadata" -> repository.parserProgress(id, event.path("totalPages").asInt());
                            case "batch" -> {
                                int total = event.path("totalPages").asInt(), end = event.path("endPage").asInt();
                                int nodeEnd = nodeCursor[0];
                                if(nodeStream) {
                                    nodeEnd = requiredNode(event,"endNode");
                                    var nextState=event.has("docxResumeState")?event.path("docxResumeState"):event.path("resumeState");
                                    if(!"DOCX_BODY_NODE".equals(event.path("parseUnit").asText()) || total!=0 || end!=0 ||
                                            event.path("startPage").asInt()!=0 || requiredNode(event,"batchIndex")!=cursor[1] ||
                                            requiredNode(event,"startNode")!=nodeCursor[0] || nodeEnd<=nodeCursor[0] ||
                                            requiredNode(nextState,"nextBodyNode")!=nodeEnd || requiredNode(nextState,"nextBatch")!=cursor[1]+1)
                                        throw new IllegalArgumentException("DOCX 节点批次或恢复检查点不连续");
                                }
                                if (total < 0 || total > 1000 || (total > 0 && (event.path("startPage").asInt() != cursor[0] + 1 || end <= cursor[0] || end > total)))
                                    throw new IllegalArgumentException("解析批次页码不连续");
                                var blocks = event.path("blocks").isEmpty() ? List.<DocumentRepository.Block>of()
                                        : adaptive!=null ? adaptive.sourceUnits(event,cursor[2],"b"+cursor[1]+":")
                                        : chunker.split(event, cursor[2], "b" + cursor[1] + ":");
                                chars[0] += blocks.stream().mapToLong(b -> b.text().length()).sum();
                                if (chars[0] > 10_000_000) throw new IllegalArgumentException("文档文本超过容量上限");
                                for (var warning : event.path("warnings")) warnings.add(warning.asText());
                                if(adaptive!=null) adaptive.commitBatch(id,cursor[1],blocks,end,total,mapper.writeValueAsString(warnings),event);
                                else repository.appendBatch(id, cursor[1], blocks, end, total, mapper.writeValueAsString(warnings));
                                cursor[0] = end; cursor[1]++; cursor[2] += blocks.size(); nodeCursor[0]=nodeEnd;
                                log.info("document_batch_committed documentId={} batch={} pages={}/{} blocks={} elapsedMs={}",
                                        id, cursor[1], end, total, cursor[2], TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started));
                            }
                            case "complete" -> {
                                if(nodeStream && (!"DOCX_BODY_NODE".equals(event.path("parseUnit").asText()) ||
                                        requiredNode(event,"totalBodyNodes")!=nodeCursor[0] || requiredNode(event,"endNode")!=nodeCursor[0]))
                                    throw new IllegalArgumentException("DOCX 解析流未覆盖完整正文节点");
                                if (cursor[0] != event.path("totalPages").asInt()) throw new IllegalStateException("解析流未覆盖完整文档");
                                if(nodeStream)repository.parserWarnings(id,mapper.writeValueAsString(warnings));
                                repository.parsed(id);
                                if(adaptive!=null) adaptive.finish(id);
                            }
                            case "heartbeat" -> repository.heartbeat(id);
                            default -> throw new IllegalStateException("未知的解析事件");
                        }
                    };
                    if (resumeState == null) parser.stream(file, doc.filename(), cursor[0], cursor[1], heading, mode, consumer);
                    else parser.stream(file, doc.filename(), cursor[0], cursor[1], heading, mode, resumeState, consumer);
                } finally { Files.deleteIfExists(file); }
            } catch (Exception error) {
                if(error instanceof java.util.concurrent.CancellationException||"CANCELLED".equals(repository.get(id).status()))return;
                if (error instanceof InterruptedException) Thread.currentThread().interrupt();
                requireLock(lock);
                boolean retry = !(error instanceof IllegalArgumentException) && repository.get(id).attempts() < 3;
                repository.failed(id, error instanceof IllegalArgumentException ? error.getMessage()
                        : "解析暂时失败，已解析的原文内容块已保存，可从检查点重试。", retry);
                log.warn("document_parsing_failed documentId={} retry={}", id, retry, error);
                if (retry) throw new IllegalStateException("文档任务等待重试", error);
            }
        } finally {
            if (gate != null && gate.isHeldByCurrentThread()) gate.unlock();
            if (lock.isHeldByCurrentThread()) lock.unlock();
        }
    }
    private void requireLock(RLock lock) {
        if (!lock.isHeldByCurrentThread()) throw new IllegalStateException("文档执行锁已失效");
    }
    String parserMode(DocumentRecord doc) {
        if(!doc.filename().toLowerCase(Locale.ROOT).endsWith(".pdf"))return docxParser.toUpperCase(Locale.ROOT);
        boolean forced=Arrays.stream(doclingDocumentIds.split(",")).map(String::strip).anyMatch(doc.id()::equals);
        return forced?"DOCLING":pdfParser.toUpperCase(Locale.ROOT);
    }
    String parserMode(DocumentRecord doc,String resumeState) {
        if(doc.parseBatches()>0 && resumeState!=null) {
            try {
                String pinned=new ObjectMapper().readTree(resumeState).path("contract").path("mode").asText("");
                if(Set.of("AUTO","HIGH_ACCURACY","FAST","DOCLING").contains(pinned))return pinned;
            } catch(com.fasterxml.jackson.core.JsonProcessingException invalid) {
                throw new IllegalArgumentException("解析恢复检查点格式错误",invalid);
            }
        }
        if(!doc.filename().toLowerCase(Locale.ROOT).endsWith(".pdf") && doc.parseBatches()>0) {
            if(resumeState!=null) {
                try { if(new ObjectMapper().readTree(resumeState).has("nextBodyNode"))return "DOCX_STREAM"; }
                catch(com.fasterxml.jackson.core.JsonProcessingException invalid) { throw new IllegalArgumentException("DOCX 恢复检查点格式错误",invalid); }
            }
            // Previously committed Docling batches keep their original whole-DOCX semantics.
            return "DOCLING";
        }
        return parserMode(doc);
    }
    private static int requiredNode(com.fasterxml.jackson.databind.JsonNode event,String field) {
        var value=event.path(field);
        if(!value.isIntegralNumber() || !value.canConvertToInt() || value.asInt()<0)
            throw new IllegalArgumentException("DOCX 检查点缺少有效节点或批次字段："+field);
        return value.asInt();
    }
    private void requireCompleteDocx(String id,com.fasterxml.jackson.databind.JsonNode event,Set<String> warnings,ObjectMapper mapper)
            throws com.fasterxml.jackson.core.JsonProcessingException {
        for(String field:List.of("warnings","parseWarnings"))for(var warning:event.path(field))warnings.add(warning.asText());
        for(String field:List.of("docxResumeState","resumeState"))for(var warning:event.path(field).path("warnings"))warnings.add(warning.asText());
        boolean unsupported=warnings.stream().anyMatch(warning->warning.startsWith("DOCX_UNSUPPORTED_") || warning.startsWith("DOCX_STREAM_PARTIAL:"));
        if(event.path("partial").asBoolean(false) || unsupported) {
            warnings.add("DOCX_STREAM_PARTIAL: 检测到未完整保留的内容；已停止正式入库，请使用 Docling 重新解析。");
            repository.parserWarnings(id,mapper.writeValueAsString(warnings));
            throw new IllegalArgumentException("DOCX 流式解析仅保留了部分内容，已停止完成确认；请使用 Docling 重新解析");
        }
    }
}
