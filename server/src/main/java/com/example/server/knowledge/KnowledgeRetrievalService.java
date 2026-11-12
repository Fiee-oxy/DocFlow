package com.example.server.knowledge;

import com.example.server.document.adaptive.AdaptiveQueryService;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.stereotype.Service;
import java.util.*;

/** Single application boundary for UI and future enterprise protocol adapters. */
@Service
public class KnowledgeRetrievalService {
    private final KnowledgeBaseService bases;
    private final AdaptiveQueryService queries;
    public KnowledgeRetrievalService(KnowledgeBaseService bases,AdaptiveQueryService queries){this.bases=bases;this.queries=queries;}
    public record Request(List<String> knowledgeBaseIds,List<String> documentIds,String question,String requestId,Integer limit){}
    public JsonNode search(KnowledgeIdentity identity,Request request)throws Exception{
        var scope=bases.resolveScope(identity.userId(),request.knowledgeBaseIds(),request.documentIds());
        return queries.preview(identity.userId(),scope,request.question(),request.limit()==null?100:request.limit());
    }
    public JsonNode answer(KnowledgeIdentity identity,Request request)throws Exception{
        var scope=bases.resolveScope(identity.userId(),request.knowledgeBaseIds(),request.documentIds());
        return queries.submit(identity.userId(),scope,request.question(),request.requestId());
    }
}
