package com.example.server.document.adaptive;

import com.example.server.document.DocumentRepository;
import com.fasterxml.jackson.databind.*;
import okhttp3.*;
import org.springframework.stereotype.Component;
import jakarta.annotation.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;

/** Independent service URL is mandatory: a shared non-preemptible model would delay foreground work. */
@Component
public class RetrievalShadowRunner {
    private final RetrievalRoutingProperties properties;private final DocumentRepository documents;private final ObjectMapper mapper;
    private ThreadPoolExecutor executor;
    private final AtomicLong accepted=new AtomicLong(),dropped=new AtomicLong(),completed=new AtomicLong(),failed=new AtomicLong();
    private record Comparison(Long owner,String documentId,String version,String requestId,List<String> baseline,List<String> routed,double elapsedMs){}
    private final ConcurrentLinkedDeque<Comparison> observations=new ConcurrentLinkedDeque<>();
    public RetrievalShadowRunner(RetrievalRoutingProperties properties,DocumentRepository documents,ObjectMapper mapper){this.properties=properties;this.documents=documents;this.mapper=mapper;}
    @PostConstruct public void initialize(){var s=properties.getShadow();executor=new ThreadPoolExecutor(s.getWorkers(),s.getWorkers(),0,TimeUnit.SECONDS,
        new ArrayBlockingQueue<>(s.getQueueCapacity()),r->{var t=new Thread(r,"retrieval-shadow");t.setDaemon(true);return t;},new ThreadPoolExecutor.AbortPolicy());}
    public void submit(Long owner,AdaptiveDocumentRepository.State state,String question,List<String> sections,String searchQuestion,boolean titleBoost,List<JsonNode> baseline,AdaptiveSettings settings){
        if(!properties.isEnabled()||properties.getMode()!=RetrievalRoutingProperties.RunMode.SHADOW||ThreadLocalRandom.current().nextDouble()>=properties.getShadow().getSampleRate())return;
        if(properties.getShadow().getUrl().replaceAll("/+$","").equals(settings.retrievalUrl().replaceAll("/+$",""))){dropped.incrementAndGet();return;}
        var baselineIds=baseline.stream().map(h->h.path("chunk_id").asText()).toList();
        String requestId=Optional.ofNullable(org.slf4j.MDC.get("requestId")).orElseGet(()->UUID.randomUUID().toString());
        try{executor.execute(()->{
            try{
                documents.owned(state.documentId(),owner); // Reauthorize after queue delay.
                var body=new LinkedHashMap<String,Object>();body.put("owner_id",owner);body.put("scopes",List.of(Map.of("document_id",state.documentId(),"index_version",state.version())));
                body.put("question",searchQuestion);body.put("rerank_question",question);body.put("section_ids",List.copyOf(sections));body.put("title_boost",titleBoost);
                body.put("focus_query",titleBoost?"":searchQuestion);body.put("dense_top_k",settings.denseTopK());body.put("sparse_top_k",settings.sparseTopK());
                body.put("fusion_top_k",settings.fusionTopK());body.put("final_top_k",settings.finalTopK());body.put("rrf_k",settings.rrfK());
                body.putAll(properties.fallbackParameters());body.put("rules_enabled",properties.getRules());body.put("request_id",requestId);
                var client=new OkHttpClient.Builder().callTimeout(properties.getShadow().getTimeoutSeconds(),TimeUnit.SECONDS).connectTimeout(3,TimeUnit.SECONDS).build();
                var request=new Request.Builder().url(properties.getShadow().getUrl().replaceAll("/+$","")+"/retrieve-routed")
                    .post(RequestBody.create(mapper.writeValueAsString(body),MediaType.get("application/json")));
                if(settings.retrievalKey()!=null&&!settings.retrievalKey().isBlank())request.header("Authorization","Bearer "+settings.retrievalKey());
                try(var response=client.newCall(request.build()).execute()){
                    if(!response.isSuccessful()||response.body()==null)throw new IllegalStateException("shadow unavailable");
                    var result=mapper.readTree(response.body().string());var ids=new ArrayList<String>();
                    if(!result.path("selected").isArray())throw new IllegalStateException("invalid shadow response");
                    for(var hit:result.path("selected")){
                        if(!state.documentId().equals(hit.path("doc_id").asText())||!state.version().equals(hit.path("index_version").asText()))throw new IllegalStateException("shadow scope mismatch");
                        ids.add(hit.path("chunk_id").asText());
                    }
                    completed.incrementAndGet();
                    observations.addLast(new Comparison(owner,state.documentId(),state.version(),requestId,baselineIds,List.copyOf(ids),result.path("timingMs").path("total").asDouble()));
                    while(observations.size()>128)observations.pollFirst();
                    org.slf4j.LoggerFactory.getLogger(getClass()).info("retrieval_shadow requestId={} sameCandidates={} baselineCount={} shadowCount={} totalMs={}",requestId,baselineIds.equals(ids),baselineIds.size(),ids.size(),result.path("timingMs").path("total").asDouble());
                }
            }catch(Exception error){failed.incrementAndGet();} // Observability cannot change foreground outcome.
        });accepted.incrementAndGet();}catch(RejectedExecutionException full){dropped.incrementAndGet();}
    }
    public Map<String,Long> counters(){return Map.of("accepted",accepted.get(),"dropped",dropped.get(),"completed",completed.get(),"failed",failed.get());}
    public JsonNode observations(Long owner){
        var out=mapper.createArrayNode();for(var item:observations){if(!owner.equals(item.owner))continue;
            try{documents.owned(item.documentId,owner);}catch(RuntimeException revoked){continue;}
            var row=out.addObject().put("requestId",item.requestId).put("documentId",item.documentId).put("indexVersion",item.version).put("totalMs",item.elapsedMs);
            row.set("baselineChunkIds",mapper.valueToTree(item.baseline));row.set("routedChunkIds",mapper.valueToTree(item.routed));
        }return out;
    }
    @PreDestroy public void close(){if(executor!=null)executor.shutdownNow();}
}
