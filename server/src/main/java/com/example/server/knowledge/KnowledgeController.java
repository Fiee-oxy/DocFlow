package com.example.server.knowledge;

import com.example.server.common.Result;
import com.example.server.service.AuthService;
import org.springframework.web.bind.annotation.*;
import java.util.*;

@RestController @RequestMapping("/knowledge-bases")
public class KnowledgeController {
    private final KnowledgeBaseService service;
    public KnowledgeController(KnowledgeBaseService service){this.service=service;}
    public record CreateRequest(String name,String sourceModule){}
    @GetMapping public Result<List<KnowledgeBaseService.Base>> list(@RequestAttribute(AuthService.REQUEST_USER_ID)Long owner){return Result.ok(service.list(owner));}
    @PostMapping public Result<KnowledgeBaseService.Base> create(@RequestBody CreateRequest request,@RequestAttribute(AuthService.REQUEST_USER_ID)Long owner){
        return Result.ok(service.create(owner,request.name(),request.sourceModule()==null?"personal":request.sourceModule()));
    }
    @GetMapping("/{id}/documents") public Result<List<Map<String,Object>>> documents(@PathVariable String id,@RequestAttribute(AuthService.REQUEST_USER_ID)Long owner){
        return Result.ok(service.documents(owner,id));
    }
    @GetMapping("/documents/{id}/versions") public Result<List<Map<String,Object>>> versions(@PathVariable String id,@RequestAttribute(AuthService.REQUEST_USER_ID)Long owner){
        return Result.ok(service.versions(owner,id));
    }
}
