package com.example.server.document;

import com.example.server.common.Result;
import com.example.server.service.AuthService;
import com.example.server.document.adaptive.LocalRetrievalClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;
import java.util.Map;

@RestController
@RequestMapping("/documents")
public class ChunkPreviewController {
    private final DocumentRepository documents;
    private final JdbcTemplate jdbc;
    private final LocalRetrievalClient retrieval;
    private final ObjectMapper mapper;
    public ChunkPreviewController(DocumentRepository documents,JdbcTemplate jdbc,LocalRetrievalClient retrieval,ObjectMapper mapper){
        this.documents=documents;this.jdbc=jdbc;this.retrieval=retrieval;this.mapper=mapper;
    }
    public record Preview(int batchIndex,String strategy,int targetTokens,int maxTokens,int minPreferredTokens,boolean mergePeers){}
    @PostMapping("/{id}/chunk-preview")
    public Result<JsonNode> preview(@PathVariable String id,@RequestBody Preview options,
            @RequestAttribute(AuthService.REQUEST_USER_ID) Long user)throws Exception{
        documents.owned(id,user);
        if(options.batchIndex()<0)throw new IllegalArgumentException("Invalid batch index");
        String payload=jdbc.queryForObject("SELECT payload FROM document_parse_batches WHERE document_id=? AND batch_index=?",String.class,id,options.batchIndex());
        return Result.ok(retrieval.call("/chunk/preview",Map.of("document_id",id,"owner_id",user,"index_version","preview",
            "batch_index",options.batchIndex(),"batch",mapper.readTree(payload),"strategy",options.strategy(),
            "target_tokens",options.targetTokens(),"max_tokens",options.maxTokens(),"min_preferred_tokens",options.minPreferredTokens(),"merge_peers",options.mergePeers())));
    }
}
