package com.example.server.document.adaptive;

import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.springframework.stereotype.Component;
import java.util.*;

@Component
public class AdaptiveContextBuilder {
    private final TokenBudgetManager tokens;
    private final ObjectMapper mapper;
    public AdaptiveContextBuilder(TokenBudgetManager tokens,ObjectMapper mapper){this.tokens=tokens;this.mapper=mapper;}
    /** Only these fields enter the model. Coordinates, versions and storage metadata stay server-side. */
    public List<JsonNode> promptEvidence(List<JsonNode> sources){
        return sources.stream().map(source -> (JsonNode)mapper.createObjectNode()
                .put("id",source.path("id").asText()).put("text",source.path("text").asText())).toList();
    }
    public long cost(List<JsonNode> sources)throws Exception{return tokens.count(mapper.writeValueAsString(promptEvidence(sources)));}
    public int adaptiveBudget(List<List<JsonNode>> sources,int preferred,int available)throws Exception{
        // A preference is not a hard fact-answer cap. Expand only for actual retrieved text, never full documents.
        int ceiling=Math.min(available,maximumEvidenceBytes);
        var all=sources.stream().flatMap(List::stream).toList();
        return Math.min(ceiling,Math.max(Math.min(preferred,ceiling),(int)Math.min(Integer.MAX_VALUE,cost(all)+1)));
    }
    @org.springframework.beans.factory.annotation.Value("${document.adaptive.max-evidence-bytes:20000}")
    private int maximumEvidenceBytes=20000;
    /** Round robin grants each document an evidence opportunity before spending the remaining budget. */
    public List<JsonNode> select(List<List<JsonNode>> perDocument,int budget)throws Exception{
        var selected=new ArrayList<JsonNode>();Set<String> seen=new HashSet<>();long used=2;
        int maximum=perDocument.stream().mapToInt(List::size).max().orElse(0);
        for(int rank=0;rank<maximum;rank++)for(var document:perDocument){
            if(rank>=document.size())continue;var source=document.get(rank);
            String key=source.path("doc_id").asText()+":"+source.path("text").asText();
            if(!seen.add(key))continue;
            long cost=tokens.count(mapper.writeValueAsString(promptEvidence(List.of(source)).getFirst()))+1;
            if(used+cost>budget)continue;selected.add(source);used+=cost;
        }
        selected.sort(Comparator.comparing((JsonNode s)->s.path("doc_id").asText()).thenComparingInt(s->s.path("blockIndex").asInt()));
        return selected;
    }
    public List<List<JsonNode>> partition(List<JsonNode> sources,int budget)throws Exception{
        var groups=new ArrayList<List<JsonNode>>();var current=new ArrayList<JsonNode>();long used=2;
        var bounded=new ArrayList<JsonNode>();
        for(var source:sources){
            if(tokens.count(mapper.writeValueAsString(source))+3<=budget){bounded.add(source);continue;}
            String text=source.path("text").asText();int offset=0;
            while(offset<text.length()){
                int low=offset+1,high=text.length(),best=offset;
                while(low<=high){int mid=(low+high)>>>1;var part=(ObjectNode)source.deepCopy();
                    part.put("id",source.path("id").asText()+":part"+offset);part.put("text",text.substring(offset,mid));
                    if(tokens.count(mapper.writeValueAsString(part))+3<=budget){best=mid;low=mid+1;}else high=mid-1;}
                if(best==offset)throw new IllegalArgumentException("来源元数据超过分析预算");
                if(best<text.length()&&Character.isHighSurrogate(text.charAt(best-1)))best--;
                if(best<=offset)throw new IllegalArgumentException("分析预算不足以容纳原文字符");
                var part=(ObjectNode)source.deepCopy();part.put("id",source.path("id").asText()+":part"+offset);
                part.put("text",text.substring(offset,best));bounded.add(part);offset=best;
            }
        }
        for(var source:bounded){
            long cost=tokens.count(mapper.writeValueAsString(source))+1;
            if(cost>budget)throw new IllegalArgumentException("单条原文超过分析预算，需先完成检索分块");
            if(!current.isEmpty()&&used+cost>budget){groups.add(current);current=new ArrayList<>();used=2;}
            current.add(source);used+=cost;
        }
        if(!current.isEmpty())groups.add(current);return groups;
    }
    public List<JsonNode> evidence(JsonNode hit,String filename){
        var out=new ArrayList<JsonNode>();for(var raw:hit.path("sources")){
            ObjectNode source=raw.deepCopy();source.put("id",hit.path("chunk_id").asText());
            source.put("doc_id",hit.path("doc_id").asText());source.put("filename",filename);
            source.put("chunk_id",hit.path("chunk_id").asText());source.put("index_version",hit.path("index_version").asText());
            source.put("blockIndex",hit.path("block_index").asInt());
            source.put("section_id",hit.path("section_id").asText());out.add(source);
        }return out;
    }
}
