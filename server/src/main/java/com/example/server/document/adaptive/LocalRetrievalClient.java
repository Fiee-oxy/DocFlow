package com.example.server.document.adaptive;

import com.example.server.common.ErrorCode;
import com.example.server.exception.BusinessException;
import com.fasterxml.jackson.databind.*;
import okhttp3.*;
import org.springframework.stereotype.Component;
import java.util.*;
import java.util.concurrent.TimeUnit;

@Component
public class LocalRetrievalClient {
    private final AdaptiveSettings settings;
    private final ObjectMapper mapper;
    @org.springframework.beans.factory.annotation.Value("${document.retrieval.routing-enabled:false}") private boolean routingEnabled=false;
    @org.springframework.beans.factory.annotation.Autowired(required=false) private RetrievalRoutingProperties routingProperties;
    @org.springframework.beans.factory.annotation.Autowired(required=false) private RetrievalShadowRunner shadowRunner;
    @org.springframework.beans.factory.annotation.Value("${document.chunk.target-tokens:350}") private int targetTokens=350;
    @org.springframework.beans.factory.annotation.Value("${document.chunk.max-tokens:480}") private int maxTokens=480;
    @org.springframework.beans.factory.annotation.Value("${document.chunk.min-preferred-tokens:80}") private int minimumTokens=80;
    @org.springframework.beans.factory.annotation.Value("${document.chunk.merge-peers:true}") private boolean mergePeers=true;
    @org.springframework.beans.factory.annotation.Value("${document.chunk.strategy:legacy}") private String chunkStrategy="legacy";
    @org.springframework.beans.factory.annotation.Value("${document.chunk.hierarchy-enabled:false}") private boolean hierarchyEnabled=false;
    @org.springframework.beans.factory.annotation.Value("${document.chunk.parent-target-tokens:1000}") private int parentTargetTokens=1000;
    @org.springframework.beans.factory.annotation.Value("${document.chunk.parent-max-tokens:1500}") private int parentMaxTokens=1500;
    @org.springframework.beans.factory.annotation.Autowired(required=false) private ChunkConfigurationStore chunkConfigurations;
    private final OkHttpClient http=new OkHttpClient.Builder().connectTimeout(5,TimeUnit.SECONDS)
            .readTimeout(180,TimeUnit.SECONDS).callTimeout(200,TimeUnit.SECONDS).build();
    public LocalRetrievalClient(AdaptiveSettings settings,ObjectMapper mapper){this.settings=settings;this.mapper=mapper;}
    @jakarta.annotation.PostConstruct
    void validateChunkConfiguration(){
        if(!"legacy".equals(chunkStrategy))
            throw new IllegalArgumentException("Production ingestion uses legacy chunking; experimental strategies are preview/evaluation only");
        if(targetTokens<32||targetTokens>480||maxTokens<32||maxTokens>480||minimumTokens<1||minimumTokens>480)
            throw new IllegalArgumentException("Invalid document.chunk configuration");
        if(parentTargetTokens<480||parentTargetTokens>2000||parentMaxTokens<parentTargetTokens||parentMaxTokens>3000)
            throw new IllegalArgumentException("Invalid parent chunk token limits");
    }
    public boolean configured(){return settings.retrievalUrl()!=null&&!settings.retrievalUrl().isBlank();}
    public JsonNode expansionPolicy(String question)throws Exception{
        var plan=call("/query/expand",Map.of("question",question,"include_routing_policy",routingActive()));
        var identity=mapper.createObjectNode();
        for(String field:List.of("policy_version","enabled","dictionary_version","route_weight","max_terms")){
            if(!plan.hasNonNull(field))throw new IllegalStateException("同义词扩展服务缺少配置版本，请更新检索服务");
            identity.set(field,plan.get(field));
        }
        identity.put("retrievalRouterEnabled",routingActive()).put("retrievalRouterVersion","retrieval-router-v2");
        if(routingProperties!=null)identity.set("retrievalRouting",mapper.valueToTree(routingProperties.identity()));
        if(plan.has("routing_policy"))identity.set("retrievalRoutingPolicy",plan.path("routing_policy"));
        else if(routingActive())throw new IllegalStateException("检索路由服务缺少规则版本，请更新服务");
        return identity;
    }
    boolean routingActive(){return routingProperties==null?routingEnabled:routingProperties.routed();}
    public boolean localRulesEnabled(){return routingActive();}
    public RouteDecision routeDecision(Long owner,AdaptiveDocumentRepository.State state,String question,List<String> sections)throws Exception{
        var scope=new RetrievalScope(owner,List.of(new RetrievalScope.DocumentVersion(state.documentId(),state.version())),List.copyOf(sections),List.of());
        var context=new QueryContext(question,owner,List.of(state.documentId()),true,false,null,
            Map.of("resolvedSectionIds",sections),scope);
        var response=call("/query/route",Map.of("question",question,"context",context,"rules_enabled",routingProperties==null?Map.of():routingProperties.getRules()));
        if(!"retrieval-router-v2".equals(response.path("ruleVersion").asText()))throw new IllegalStateException("路由规则版本不匹配");
        var mode=RetrievalMode.valueOf(response.path("mode").asText());if(mode==RetrievalMode.NONE)throw new IllegalStateException("文档问答不可跳过证据");
        var matched=new ArrayList<String>();response.path("matchedRules").forEach(r->matched.add(r.asText()));
        return new RouteDecision(mode,response.path("reasonCode").asText(),matched,scope,response.path("fallbackEnabled").asBoolean(),response.path("ruleVersion").asText());
    }
    public JsonNode call(String path,Object payload)throws Exception{
        if(!configured())throw new BusinessException(ErrorCode.SERVICE_UNAVAILABLE,"本地 BGE / Milvus 服务尚未配置，不能执行混合检索");
        var request=new Request.Builder().url(settings.retrievalUrl().replaceAll("/+$","")+path)
                .post(RequestBody.create(mapper.writeValueAsString(payload),MediaType.get("application/json")));
        if(settings.retrievalKey()!=null&&!settings.retrievalKey().isBlank())request.header("Authorization","Bearer "+settings.retrievalKey());
        for(int attempt=0;attempt<5;attempt++)try(var response=http.newCall(request.build()).execute()){
            if(response.code()==429&&attempt<4){response.close();Thread.sleep(150L*(1L<<attempt));continue;}
            if(!response.isSuccessful()||response.body()==null){
                String type=switch(response.code()){case 401,403->"PERMISSION_DENIED";case 409->"INDEX_NOT_READY";case 429->"RETRIEVAL_BUSY";case 504->"RETRIEVAL_TIMEOUT";default->"RETRIEVAL_UNAVAILABLE";};
                if(response.body()!=null){try{String reported=mapper.readTree(response.body().string()).path("detail").path("failureType").asText();
                    if(Set.of("QUERY_EMBEDDING_FAILED","INDEX_NOT_READY","PAGE_SCOPE_NOT_INDEXED","RETRIEVAL_TIMEOUT","RETRIEVAL_UNAVAILABLE").contains(reported))type=reported;
                }catch(Exception ignored){}}
                throw new RetrievalServiceException(type,response.code());
            }
            return mapper.readTree(response.body().string());
        }catch(java.net.SocketTimeoutException timeout){throw new RetrievalServiceException("RETRIEVAL_TIMEOUT",504);}
        catch(java.io.InterruptedIOException timeout){throw new RetrievalServiceException("RETRIEVAL_TIMEOUT",504);}
        catch(java.net.ConnectException unavailable){throw new RetrievalServiceException("RETRIEVAL_UNAVAILABLE",503);}
        catch(java.io.IOException unavailable){throw new RetrievalServiceException("RETRIEVAL_UNAVAILABLE",503);}
        throw new IllegalStateException("本地检索重试未完成");
    }
    public JsonNode index(AdaptiveDocumentRepository.State s,JsonNode batch)throws Exception{
        Map<String,Object> config=Map.of("strategy",chunkStrategy,"strategy_revision",chunkStrategy+"-v1","max_tokens",maxTokens,"target_tokens",targetTokens,
                "min_preferred_tokens",minimumTokens,"merge_peers",mergePeers,"hierarchy_enabled",hierarchyEnabled,
                "parent_target_tokens",parentTargetTokens,"parent_max_tokens",parentMaxTokens);
        if(chunkConfigurations!=null)config=chunkConfigurations.freeze(s,config);
        if(!"legacy".equals(config.get("strategy")))
            throw new IllegalStateException("Experimental index configuration cannot resume production ingestion; rebuild with a new legacy index version");
        var payload=new LinkedHashMap<String,Object>(config);
        payload.put("document_id",s.documentId());payload.put("owner_id",s.ownerId());payload.put("index_version",s.version());
        payload.put("batch_index",s.indexedBatches());payload.put("batch",batch);
        return call("/index",payload);
    }
    public List<JsonNode> retrieve(Long owner,AdaptiveDocumentRepository.State state,String question,List<String> sections)throws Exception{
        return retrieveTrace(owner,state,question,sections).selected();
    }
    public record RetrievalTrace(JsonNode dense,JsonNode sparse,List<JsonNode> fused,JsonNode reranked,List<JsonNode> selected,JsonNode routing){
        public RetrievalTrace(JsonNode dense,JsonNode sparse,List<JsonNode> fused,JsonNode reranked,List<JsonNode> selected){
            this(dense,sparse,fused,reranked,selected,null);
        }
    }
    public RetrievalTrace retrieveTrace(Long owner,AdaptiveDocumentRepository.State state,String question,List<String> sections)throws Exception{
        return retrieveTrace(owner,state,question,sections,question,true);
    }
    public RetrievalTrace retrieveTrace(Long owner,AdaptiveDocumentRepository.State state,String question,List<String> sections,
            String searchQuestion,boolean titleBoost)throws Exception{
        return retrieveTrace(owner,state,question,sections,searchQuestion,titleBoost,List.of());
    }
    public RetrievalTrace retrieveTrace(Long owner,AdaptiveDocumentRepository.State state,String question,List<String> sections,
            String searchQuestion,boolean titleBoost,List<String> variants)throws Exception{
        if(routingActive()||!variants.isEmpty()){
            var payload=new LinkedHashMap<String,Object>();payload.put("owner_id",owner);
            payload.put("scopes",List.of(Map.of("document_id",state.documentId(),"index_version",state.version())));
            payload.put("question",searchQuestion);payload.put("rerank_question",question);payload.put("section_ids",sections);
            payload.put("dense_top_k",settings.denseTopK());payload.put("sparse_top_k",settings.sparseTopK());
            payload.put("fusion_top_k",settings.fusionTopK());payload.put("final_top_k",settings.finalTopK());payload.put("rrf_k",settings.rrfK());
            payload.put("title_boost",titleBoost);payload.put("focus_query",titleBoost?"":searchQuestion);
            payload.put("query_variants",variants);
            payload.put("execution_mode",routingActive()?"ROUTED":"BASELINE");
            payload.put("request_id",Optional.ofNullable(org.slf4j.MDC.get("requestId")).orElseGet(()->UUID.randomUUID().toString()));
            if(routingProperties!=null)payload.putAll(routingProperties.fallbackParameters());
            else payload.put("fallback_enabled",true);
            payload.put("index_ready",state.ready()||state.partial());
            payload.put("rules_enabled",routingProperties==null?Map.of():routingProperties.getRules());
            var response=call("/retrieve-routed",payload);
            for(String field:List.of("dense","sparse","fused","reranked","selected")){
                if(!response.path(field).isArray())throw new IllegalStateException("路由检索响应缺少候选数组");
                for(var hit:response.path(field))validateScope(hit,state);
            }
            var fused=new ArrayList<JsonNode>();response.path("fused").forEach(fused::add);
            var selected=new ArrayList<JsonNode>();response.path("selected").forEach(selected::add);
            if(selected.size()>settings.finalTopK())throw new IllegalStateException("路由检索返回超过候选上限");
            if(!response.path("routing").isObject() || !"retrieval-router-v2".equals(response.path("routing").path("revision").asText()))
                throw new IllegalStateException("路由检索策略版本不匹配");
            var trace=(com.fasterxml.jackson.databind.node.ObjectNode)response.path("routing").deepCopy();trace.set("timingMs",response.path("timingMs"));
            try{if(routingProperties==null||routingProperties.isMetricsEnabled())org.slf4j.LoggerFactory.getLogger(getClass()).info(
                "retrieval_route requestId={} mode={} ruleVersion={} fallback={} candidates={} totalMs={}",
                payload.get("request_id"),trace.path("finalRoute").asText(),trace.path("ruleVersion").asText(),trace.path("fallback").asBoolean(),fused.size(),response.path("timingMs").path("total").asDouble());}catch(RuntimeException ignored){}
            return new RetrievalTrace(response.path("dense"),response.path("sparse"),fused,response.path("reranked"),selected,trace);
        }
        var result=call("/search",Map.of("owner_id",owner,"scopes",List.of(Map.of("document_id",state.documentId(),"index_version",state.version())),
                "question",searchQuestion,"section_ids",sections,"dense_top_k",settings.denseTopK(),"sparse_top_k",settings.sparseTopK()));
        var fused=fuse(result.path("dense"),result.path("sparse"),settings.rrfK(),settings.fusionTopK());
        for(var list:List.of(result.path("dense"),result.path("sparse")))for(var hit:list)validateScope(hit,state);
        if(fused.isEmpty())return new RetrievalTrace(result.path("dense"),result.path("sparse"),fused,mapper.createArrayNode(),List.of());
        var ranked=call("/rerank",Map.of("question",question,"candidates",fused,"title_boost",titleBoost,"focus_query",titleBoost?"":searchQuestion));
        for(var hit:ranked.path("candidates"))validateScope(hit,state);
        var out=new ArrayList<JsonNode>();
        for(var c:ranked.path("candidates")){
            if(!c.path("doc_id").asText().equals(state.documentId())||!c.path("index_version").asText().equals(state.version()))
                throw new IllegalStateException("检索服务返回了范围外证据");
            out.add(c);if(out.size()>=settings.finalTopK())break;
        }
        try{if(shadowRunner!=null)shadowRunner.submit(owner,state,question,sections,searchQuestion,titleBoost,out,settings);}catch(RuntimeException ignored){}
        return new RetrievalTrace(result.path("dense"),result.path("sparse"),fused,ranked.path("candidates"),out);
    }
    private void validateScope(JsonNode hit,AdaptiveDocumentRepository.State state){
        if(!hit.path("doc_id").asText().equals(state.documentId())||!hit.path("index_version").asText().equals(state.version()))
            throw new IllegalStateException("检索服务返回了范围外证据");
    }
    public static List<JsonNode> fuse(JsonNode dense,JsonNode sparse,int k,int limit){
        Map<String,JsonNode> values=new LinkedHashMap<>();Map<String,Double> scores=new HashMap<>();
        for(var list:List.of(dense,sparse)){
            int rank=0;Set<String> seen=new HashSet<>();
            for(var hit:list){String id=hit.path("chunk_id").asText();if(!seen.add(id))continue;
                rank++;values.putIfAbsent(id,hit);scores.merge(id,1.0/(k+rank),Double::sum);}
        }
        // Protect high-ranked single-channel hits before truncating the fusion pool.
        var order=values.keySet().stream().sorted(Comparator.<String>comparingDouble(scores::get).reversed()
                .thenComparing(java.util.function.Function.identity())).toList();
        Set<String> protectedIds=new LinkedHashSet<>();
        int reserve=Math.min(5,limit/2);
        for(var list:List.of(dense,sparse)){
            int n=0;for(var hit:list){if(n++>=reserve)break;protectedIds.add(hit.path("chunk_id").asText());}
        }
        for(String id:order){if(protectedIds.size()>=limit)break;protectedIds.add(id);}
        return order.stream().filter(protectedIds::contains).map(id->{
            var hit=(com.fasterxml.jackson.databind.node.ObjectNode)values.get(id).deepCopy();
            hit.put("rrf_score",scores.get(id));return (JsonNode)hit;
        }).toList();
    }
}
