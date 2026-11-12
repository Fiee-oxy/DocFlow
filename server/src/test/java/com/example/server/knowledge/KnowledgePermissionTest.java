package com.example.server.knowledge;

import org.junit.jupiter.api.Test;
import com.example.server.document.adaptive.AdaptiveQueryService;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class KnowledgePermissionTest {
    @Test void readOnlyPolicyCannotRequestOrRebuildAnIndex()throws Exception{
        var documents=mock(com.example.server.document.DocumentRepository.class);
        var repository=mock(com.example.server.document.adaptive.AdaptiveDocumentRepository.class);
        var controller=new com.example.server.document.adaptive.AdaptiveQueryController(mock(AdaptiveQueryService.class),
                mock(com.example.server.document.adaptive.AdaptiveQueryTaskStore.class),documents,repository,new com.fasterxml.jackson.databind.ObjectMapper());
        doThrow(new SecurityException("read only")).when(documents).writable("doc",7L);
        assertThrows(SecurityException.class,()->controller.index("doc",7L));
        assertThrows(SecurityException.class,()->controller.rebuild("doc",7L));
        verifyNoInteractions(repository);
    }
    @Test void defaultPolicyRejectsOtherOwnersAndAnonymousIdentity(){
        var policy=new KnowledgePermissionConfiguration().personalPermissionChecker();
        var resource=new PermissionChecker.Resource("DOCUMENT","a",7L,"personal");
        assertTrue(policy.allowed(new KnowledgeIdentity(7L),resource,PermissionChecker.Operation.READ));
        assertFalse(policy.allowed(new KnowledgeIdentity(8L),resource,PermissionChecker.Operation.READ));
        assertThrows(SecurityException.class,()->new KnowledgeIdentity(null));
    }
    @Test void deniedScopeCannotReachPreviewOrModelTask()throws Exception{
        var bases=mock(KnowledgeBaseService.class);var queries=mock(AdaptiveQueryService.class);
        var service=new KnowledgeRetrievalService(bases,queries);
        var request=new KnowledgeRetrievalService.Request(List.of("private"),List.of("doc"),"question",UUID.randomUUID().toString(),20);
        when(bases.resolveScope(8L,request.knowledgeBaseIds(),request.documentIds())).thenThrow(new SecurityException("denied"));
        assertThrows(SecurityException.class,()->service.search(new KnowledgeIdentity(8L),request));
        assertThrows(SecurityException.class,()->service.answer(new KnowledgeIdentity(8L),request));
        verifyNoInteractions(queries);
    }
}
