package com.example.server.document.adaptive;

import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.springframework.stereotype.Component;
import org.springframework.beans.factory.annotation.Value;
import java.util.*;

/** Reads published parents only; never expands an entire section unconditionally. */
@Component
public class ParentContextExpander {
    private final AdaptiveDocumentRepository repository;private final TokenBudgetManager tokens;
    @Value("${document.chunk.parent-context-enabled:false}") private boolean enabled=false;
    @Value("${document.chunk.parent-context-max-bytes:6000}") private int maxBytes=6000;
    @Value("${document.chunk.parent-context-total-bytes:12000}") private int totalBytes=12000;
    public ParentContextExpander(AdaptiveDocumentRepository repository,TokenBudgetManager tokens){this.repository=repository;this.tokens=tokens;}
    public String identity(){return "parent-read-v2-batched:"+enabled+":"+maxBytes+":"+totalBytes;}
    public List<JsonNode> expand(String question,List<JsonNode> hits,List<JsonNode> evidence,String filename,ObjectNode trace){
        long started=System.nanoTime();
        try{return expandSources(question,hits,evidence,filename,trace);}
        finally{if(trace!=null)trace.withObject("/stageTimingMs").put("parentResolver",(System.nanoTime()-started)/1_000_000.0).put("parentResolverEnabled",enabled);}
    }
    private List<JsonNode> expandSources(String question,List<JsonNode> hits,List<JsonNode> evidence,String filename,ObjectNode trace){
        if(!enabled)return evidence;
        long started=System.nanoTime();
        var eligible=hits.stream().filter(h->!h.path("sources").isEmpty()&&h.path("sources").get(0).hasNonNull("parent_id")).toList();
        var loaded=repository.parentsForHits(eligible);var parentScores=new HashMap<String,Double>();
        var parents=new LinkedHashMap<String,JsonNode>();var matched=new HashMap<String,Set<String>>();
        for(var hit:hits){
            if(hit.path("sources").isEmpty()||!hit.path("sources").get(0).hasNonNull("parent_id"))continue;
            String expected=hit.path("sources").get(0).path("parent_id").asText();
            var parent=loaded.get(hit.path("chunk_id").asText());
            if(parent==null)continue;
            boolean member=false;for(var child:parent.path("child_ids"))if(child.asText().equals(hit.path("chunk_id").asText()))member=true;
            if(!member)continue;
            String id=parent.path("id").asText();parents.put(id,parent);matched.computeIfAbsent(id,k->new HashSet<>()).add(hit.path("chunk_id").asText());
            parentScores.merge(id,hit.path("rerank_score").asDouble(),Math::max);
        }
        boolean explanation=question.toLowerCase(Locale.ROOT).matches("(?s).*(为什么|为何|原因|解释|如何|怎么|上下文|\\bwhy\\b|\\bhow\\b(?!\\s+(?:many|much|long|old)\\b)|\\bexplain\\b|\\breason\\b).*");
        var replacements=new ArrayList<JsonNode>();var replaced=new HashSet<String>();long used=0;
        for(var entry:parents.entrySet().stream().sorted(Comparator.comparingDouble((Map.Entry<String,JsonNode> e)->parentScores.get(e.getKey())).reversed()).toList()){
            String id=entry.getKey();var parent=entry.getValue();
            if(!explanation&&matched.get(id).size()<2)continue;
            long cost=tokens.count(parent.toString());if(cost>maxBytes||used+cost>totalBytes)continue;
            var source=(ObjectNode)parent.deepCopy();source.put("doc_id",hits.getFirst().path("doc_id").asText());
            source.put("index_version",hits.getFirst().path("index_version").asText());source.put("filename",filename);
            int first=hits.stream().filter(h->matched.get(id).contains(h.path("chunk_id").asText())).mapToInt(h->h.path("block_index").asInt()).min().orElse(0);
            source.put("blockIndex",first);replacements.add(source);used+=cost;
            parent.path("child_ids").forEach(c->replaced.add(c.asText()));
        }
        if(replacements.isEmpty())return evidence;
        var result=new ArrayList<JsonNode>();for(var source:evidence)if(!replaced.contains(source.path("id").asText()))result.add(source);
        result.addAll(replacements);
        if(trace!=null){var info=trace.putObject("parentExpansion");info.put("parents",replacements.size()).put("metadataAndTextBytes",used);
            info.put("trigger",explanation?"EXPLANATION":"MULTIPLE_CHILD_HITS");info.set("sources",new ObjectMapper().valueToTree(replacements));}
        if(trace!=null)trace.withObject("/parentExpansion").put("elapsedMs",(System.nanoTime()-started)/1_000_000.0).put("batchQueries",eligible.isEmpty()?0:1);
        return result;
    }
}
