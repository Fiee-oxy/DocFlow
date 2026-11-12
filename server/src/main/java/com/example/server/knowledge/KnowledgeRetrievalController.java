package com.example.server.knowledge;

import com.example.server.common.Result;
import com.example.server.service.AuthService;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;

@RestController @RequestMapping("/knowledge")
public class KnowledgeRetrievalController {
    private final KnowledgeRetrievalService service;
    private final KnowledgeIdentityAdapter identities;
    public KnowledgeRetrievalController(KnowledgeRetrievalService service,KnowledgeIdentityAdapter identities){this.service=service;this.identities=identities;}
    @PostMapping("/search") public ResponseEntity<Result<JsonNode>> search(@RequestBody KnowledgeRetrievalService.Request request,
            @RequestAttribute(AuthService.REQUEST_USER_ID)Long owner)throws Exception{
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(Result.ok(service.search(identities.resolveAuthenticatedUser(owner),request)));
    }
    @PostMapping("/query") public ResponseEntity<Result<JsonNode>> query(@RequestBody KnowledgeRetrievalService.Request request,
            @RequestAttribute(AuthService.REQUEST_USER_ID)Long owner)throws Exception{
        return ResponseEntity.accepted().body(Result.ok(service.answer(identities.resolveAuthenticatedUser(owner),request)));
    }
}
