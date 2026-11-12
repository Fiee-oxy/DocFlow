package com.example.server.document.adaptive;

import com.example.server.document.DocumentRepository;
import com.example.server.service.AuthService;
import com.example.server.common.Result;
import com.fasterxml.jackson.databind.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.http.ResponseEntity;
import org.springframework.http.CacheControl;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import org.springframework.scheduling.annotation.Scheduled;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

@RestController
@RequestMapping("/documents")
public class AdaptiveQueryController {
    private final AdaptiveQueryService queries;private final AdaptiveQueryTaskStore tasks;
    private final DocumentRepository documents;private final AdaptiveDocumentRepository repository;private final ObjectMapper mapper;
    private org.redisson.api.RedissonClient redisson;
    @org.springframework.beans.factory.annotation.Autowired(required=false) private RetrievalShadowRunner shadow;
    @org.springframework.beans.factory.annotation.Autowired public void indexLocks(org.redisson.api.RedissonClient redisson){this.redisson=redisson;}
    private final Map<SseEmitter,Subscription> subscribers=new ConcurrentHashMap<>();
    private static class Subscription {String id;Long owner;long after,lastHeartbeat;Subscription(String id,Long owner,long after){this.id=id;this.owner=owner;this.after=after;}}
    public AdaptiveQueryController(AdaptiveQueryService queries,AdaptiveQueryTaskStore tasks,DocumentRepository documents,
            AdaptiveDocumentRepository repository,ObjectMapper mapper){this.queries=queries;this.tasks=tasks;this.documents=documents;this.repository=repository;this.mapper=mapper;}
    public record QueryRequest(List<String> documentIds,String question,String requestId){}
    public record PreviewRequest(List<String> documentIds,String question,Integer limit){}
    public record SectionReadRequest(String indexVersion,List<String> sectionIds,Integer afterBlockIndex,Integer budgetTokens){}
    @GetMapping("/query/shadow-observations")
    public ResponseEntity<Result<JsonNode>> shadowObservations(@RequestAttribute(AuthService.REQUEST_USER_ID)Long owner){
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(Result.ok(shadow==null?mapper.createArrayNode():shadow.observations(owner)));
    }
    @GetMapping("/{id}/parents/{parentId}")
    public ResponseEntity<Result<JsonNode>> readParent(@PathVariable String id,@PathVariable String parentId,@RequestParam String indexVersion,
            @RequestParam(defaultValue="6000")int budgetBytes,@RequestAttribute(AuthService.REQUEST_USER_ID)Long owner){
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(Result.ok(queries.readParent(owner,id,indexVersion,parentId,budgetBytes)));
    }
    @GetMapping("/{id}/structure")
    public ResponseEntity<Result<JsonNode>> structure(@PathVariable String id,@RequestParam String indexVersion,
            @RequestParam(defaultValue="-1")int afterBatch,@RequestParam(defaultValue="1")int limit,
            @RequestAttribute(AuthService.REQUEST_USER_ID)Long owner)throws Exception{
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(Result.ok(queries.structure(owner,id,indexVersion,afterBatch,limit)));
    }
    @GetMapping("/query/diagnostics")
    public ResponseEntity<Result<JsonNode>> diagnostics(@RequestAttribute(AuthService.REQUEST_USER_ID)Long owner){
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(Result.ok(queries.diagnostics()));
    }
    @PostMapping("/{id}/sections/read")
    public Result<JsonNode> readSection(@PathVariable String id,@RequestBody SectionReadRequest request,
            @RequestAttribute(AuthService.REQUEST_USER_ID)Long owner)throws Exception{
        return Result.ok(queries.readSection(owner,id,request.indexVersion(),request.sectionIds(),request.afterBlockIndex()==null?-1:request.afterBlockIndex(),
                request.budgetTokens()==null?8000:request.budgetTokens()));
    }
    @PostMapping("/query/preview")
    public ResponseEntity<Result<JsonNode>> preview(@RequestBody PreviewRequest request,@RequestAttribute(AuthService.REQUEST_USER_ID)Long owner)throws Exception{
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(Result.ok(
                queries.preview(owner,request.documentIds(),request.question(),request.limit()==null?50:request.limit())));
    }
    @PostMapping("/query")
    public ResponseEntity<Result<JsonNode>> submit(@RequestBody QueryRequest request,@RequestAttribute(AuthService.REQUEST_USER_ID)Long owner)throws Exception{
        return ResponseEntity.accepted().body(Result.ok(queries.submit(owner,request.documentIds(),request.question(),request.requestId())));
    }
    private JsonNode authorizedTask(String id,Long owner)throws Exception{
        var task=tasks.owned(id,owner);var scope=new ArrayList<String>();
        for(var doc:mapper.readTree(task.path("document_ids").asText()))scope.add(doc.asText());
        documents.authorizeScope(scope,owner);return task;
    }
    @GetMapping("/query-tasks/{id}")
    public Result<JsonNode> status(@PathVariable String id,@RequestAttribute(AuthService.REQUEST_USER_ID)Long owner)throws Exception{
        return Result.ok(authorizedTask(id,owner));
    }
    @DeleteMapping("/query-tasks/{id}")
    public Result<Void> cancel(@PathVariable String id,@RequestAttribute(AuthService.REQUEST_USER_ID)Long owner)throws Exception{
        authorizedTask(id,owner);tasks.cancel(id);return Result.ok();
    }
    @GetMapping(value="/query-tasks/{id}/events",produces="text/event-stream")
    public SseEmitter events(@PathVariable String id,@RequestParam(defaultValue="0")long after,
            @RequestHeader(value="Last-Event-ID",required=false)Long last,
            @RequestAttribute(AuthService.REQUEST_USER_ID)Long owner)throws Exception{
        authorizedTask(id,owner);var emitter=new SseEmitter(600000L);
        subscribers.put(emitter,new Subscription(id,owner,Math.max(after,last==null?0:last)));
        emitter.onCompletion(()->subscribers.remove(emitter));emitter.onTimeout(()->subscribers.remove(emitter));
        emitter.onError(e->subscribers.remove(emitter));return emitter;
    }
    @Scheduled(fixedDelay=250)
    public void publish(){
        for(var item:subscribers.entrySet()){
            var emitter=item.getKey();var s=item.getValue();
            try{
                var task=authorizedTask(s.id,s.owner);
                var events=tasks.events(s.id,s.after);
                for(var event:events){
                    emitter.send(SseEmitter.event().id(event.get("id").toString()).name(event.get("event_type").toString())
                            .data(mapper.readTree(event.get("payload").toString())));s.after=((Number)event.get("id")).longValue();
                }
                if(events.isEmpty()&&Set.of("COMPLETED","FAILED","CANCELLED").contains(task.path("state").asText())){
                    emitter.send(SseEmitter.event().name("task_status").data(task));emitter.complete();subscribers.remove(emitter);
                }else if(System.currentTimeMillis()-s.lastHeartbeat>15000){
                    emitter.send(SseEmitter.event().comment("heartbeat"));s.lastHeartbeat=System.currentTimeMillis();
                }
            }catch(Exception e){subscribers.remove(emitter);emitter.complete();}
        }
    }
    @GetMapping("/{id}/strategy")
    public Result<JsonNode> strategy(@PathVariable String id,@RequestAttribute(AuthService.REQUEST_USER_ID)Long owner)throws Exception{
        documents.owned(id,owner);var state=repository.bootstrap(id,owner);var result=mapper.valueToTree(state);
        ((com.fasterxml.jackson.databind.node.ObjectNode)result).set("sections",mapper.valueToTree(repository.sections(id)));
        return Result.ok(result);
    }
    @PostMapping("/{id}/index")
    public ResponseEntity<Result<JsonNode>> index(@PathVariable String id,@RequestAttribute(AuthService.REQUEST_USER_ID)Long owner)throws Exception{
        documents.writable(id,owner);repository.bootstrap(id,owner);repository.requestIndex(id);
        return ResponseEntity.accepted().body(Result.ok(mapper.valueToTree(repository.state(id))));
    }
    @PostMapping("/{id}/index/rebuild")
    public ResponseEntity<Result<JsonNode>> rebuild(@PathVariable String id,@RequestAttribute(AuthService.REQUEST_USER_ID)Long owner)throws Exception{
        documents.writable(id,owner);repository.bootstrap(id,owner);
        var lock=redisson.getLock("lock:document:adaptive-index:"+id);
        if(!lock.tryLock())throw new com.example.server.exception.BusinessException(com.example.server.common.ErrorCode.CONFLICT,"索引正在处理，请稍后重建");
        try{repository.rebuild(id);}finally{if(lock.isHeldByCurrentThread())lock.unlock();}
        return ResponseEntity.accepted().body(Result.ok(mapper.valueToTree(repository.state(id))));
    }
}
