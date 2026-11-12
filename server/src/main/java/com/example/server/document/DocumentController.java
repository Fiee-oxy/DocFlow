package com.example.server.document;

import com.example.server.common.ErrorCode;
import com.example.server.common.Result;
import com.example.server.exception.BusinessException;
import com.example.server.service.AuthService;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.Executor;
import org.springframework.beans.factory.annotation.Qualifier;

@RestController
@RequestMapping("/documents")
public class DocumentController {
    private final DocumentUploadService uploads;
    private final DocumentRepository repository;
    private final DocumentQueue queue;
    private final ObjectMapper mapper;
    private final DocumentChatService chat;
    private final Executor executor;
    @org.springframework.beans.factory.annotation.Autowired private com.example.server.knowledge.KnowledgeBaseService knowledgeBases;
    public DocumentController(DocumentUploadService uploads, DocumentRepository repository, DocumentQueue queue, ObjectMapper mapper,
                              DocumentChatService chat, @Qualifier("aiTaskExecutor") Executor executor) {
        this.uploads = uploads; this.repository = repository; this.queue = queue; this.mapper = mapper;
        this.chat = chat; this.executor = executor;
    }
    public record ChatRequest(String question, String requestId) {}
    @GetMapping("/{id}/chat")
    public Result<List<JsonNode>> history(@PathVariable String id,
                            @RequestAttribute(AuthService.REQUEST_USER_ID) Long userId) throws Exception {
        return Result.ok(chat.history(id, userId));
    }
    @PostMapping("/{id}/chat")
    public CompletableFuture<Result<JsonNode>> ask(@PathVariable String id, @RequestBody ChatRequest request,
                            @RequestAttribute(AuthService.REQUEST_USER_ID) Long userId) {
        return CompletableFuture.supplyAsync(() -> {
            try { return Result.ok(chat.ask(id, userId, request.question(), request.requestId())); }
            catch (RuntimeException e) { throw e; }
            catch (Exception e) { throw new CompletionException(e); }
        }, executor);
    }
    @PostMapping("/init-upload")
    public Result<String> initialize(@RequestParam String filename, @RequestParam long size,
                                     @RequestParam(required=false) String knowledgeBaseId,
                                     @RequestParam(required=false) String previousDocumentId,
                                     @RequestAttribute(AuthService.REQUEST_USER_ID) Long userId) {
        return Result.ok(knowledgeBases.initialize(userId,knowledgeBaseId,previousDocumentId,filename,size));
    }
    @PostMapping("/upload-chunk")
    public Result<Void> upload(@RequestParam String uploadId, @RequestParam int chunkIndex,
                               @RequestParam MultipartFile file,
                               @RequestParam(required=false) String sha256,
                               @RequestAttribute(AuthService.REQUEST_USER_ID) Long userId) throws Exception {
        uploads.upload(uploadId, chunkIndex, file, userId, sha256);
        return Result.ok();
    }
    @GetMapping("/upload-status")
    public Result<List<Integer>> parts(@RequestParam String uploadId,
                                       @RequestAttribute(AuthService.REQUEST_USER_ID) Long userId) {
        return Result.ok(uploads.parts(uploadId, userId));
    }
    @PostMapping("/complete-upload")
    public ResponseEntity<Result<DocumentRecord.View>> complete(@RequestParam String uploadId,
                                         @RequestAttribute(AuthService.REQUEST_USER_ID) Long userId) {
        DocumentRecord.View result = uploads.complete(uploadId, userId);
        if (!List.of("FAILED", "COMPLETED").contains(result.status())) queue.send(uploadId);
        return ResponseEntity.accepted().body(Result.ok(result));
    }
    @GetMapping
    public Result<List<DocumentRecord.View>> list(@RequestAttribute(AuthService.REQUEST_USER_ID) Long userId) {
        return Result.ok(repository.list(userId));
    }
    @GetMapping("/{id}")
    public Result<DocumentRecord.View> status(@PathVariable String id,
                                            @RequestAttribute(AuthService.REQUEST_USER_ID) Long userId) {
        return Result.ok(repository.owned(id, userId).view());
    }
    @GetMapping("/{id}/report")
    public Result<JsonNode> report(@PathVariable String id,
                                   @RequestAttribute(AuthService.REQUEST_USER_ID) Long userId) throws Exception {
        DocumentRecord doc = repository.owned(id, userId);
        if (!"COMPLETED".equals(doc.status()) || doc.reportJson() == null)
            throw new BusinessException(ErrorCode.CONFLICT, "文档知识归纳尚未完成");
        return Result.ok(mapper.readTree(doc.reportJson()));
    }
    @GetMapping("/{id}/chunks")
    public Result<JsonNode> chunks(@PathVariable String id,
            @RequestParam(defaultValue="1") int page, @RequestParam(defaultValue="10") int size,
            @RequestAttribute(AuthService.REQUEST_USER_ID) Long userId) throws Exception {
        DocumentRecord doc = repository.owned(id, userId);
        if (page < 1 || page > 100000 || size < 1 || size > 50)
            throw new IllegalArgumentException("内容块分页参数不合法");
        var result = mapper.createObjectNode();
        result.put("totalBlocks", doc.totalBlocks()); result.put("page", page);
        result.put("parseComplete", doc.parseComplete());
        result.set("warnings", doc.warningsJson() == null ? mapper.createArrayNode() : mapper.readTree(doc.warningsJson()));
        var items = result.putArray("chunks");
        for (var block : repository.chunks(id, (page - 1) * size, size)) {
            var item = items.addObject(); item.put("index", block.index()); item.put("section", block.section());
            item.put("text", block.text()); item.set("sources", mapper.readTree(block.sourceJson()));
        }
        return Result.ok(result);
    }
    @GetMapping("/{id}/failures")
    public Result<List<DocumentRepository.FailureEvent>> failures(@PathVariable String id,
            @RequestParam(defaultValue="100") int limit, @RequestParam(required=false) Long beforeId,
            @RequestAttribute(AuthService.REQUEST_USER_ID) Long userId) {
        repository.owned(id, userId);
        return Result.ok(repository.failures(id, limit, beforeId));
    }
    @PostMapping("/{id}/retry")
    public ResponseEntity<Result<DocumentRecord.View>> retry(@PathVariable String id,
                                   @RequestAttribute(AuthService.REQUEST_USER_ID) Long userId) {
        DocumentRecord doc = repository.owned(id, userId);
        repository.writable(id,userId);
        uploads.requireRetryable(id);
        if ("FAILED".equals(doc.status())) {
            repository.queue(id, "FAILED");
        }
        else if (!List.of("QUEUED", "RETRYING", "PROCESSING").contains(doc.status()))
            throw new BusinessException(ErrorCode.CONFLICT, "当前文档不需要重试");
        queue.send(id);
        return ResponseEntity.accepted().body(Result.ok(repository.get(id).view()));
    }
    @PostMapping("/{id}/cancel-parsing")
    public Result<DocumentRecord.View> cancelParsing(@PathVariable String id,@RequestAttribute(AuthService.REQUEST_USER_ID) Long userId){
        repository.writable(id,userId);repository.cancelParsing(id);return Result.ok(repository.get(id).view());
    }
    @GetMapping("/{id}/parser-audit")
    public Result<JsonNode> parserAudit(@PathVariable String id,@RequestAttribute(AuthService.REQUEST_USER_ID) Long userId)throws Exception{
        repository.owned(id,userId);var result=mapper.createArrayNode();for(String row:repository.parserAudits(id))result.add(mapper.readTree(row));return Result.ok(result);
    }
    @GetMapping("/{id}/ir")
    public ResponseEntity<String> ir(@PathVariable String id,@RequestParam(defaultValue="0") int batch,
            @RequestParam(defaultValue="json") String format,@RequestAttribute(AuthService.REQUEST_USER_ID) Long userId)throws Exception{
        repository.owned(id,userId);if(batch<0)throw new IllegalArgumentException("batch 不能为负数");
        String body=com.example.server.document.parsing.DocumentIRCodec.export(mapper.readTree(repository.parserIR(id,batch)),format);
        String type=switch(format){case "json"->"application/json";case "jsonl"->"application/x-ndjson";case "markdown"->"text/markdown";default->throw new IllegalArgumentException("未知导出格式");};
        return ResponseEntity.ok().header("Content-Type",type+";charset=UTF-8").body(body);
    }
}
