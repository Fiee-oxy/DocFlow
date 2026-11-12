package com.example.server.document.adaptive;

import com.example.server.document.*;
import com.fasterxml.jackson.databind.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.core.io.ClassPathResource;
import org.springframework.aop.framework.ProxyFactory;
import org.springframework.transaction.interceptor.*;
import org.springframework.transaction.annotation.AnnotationTransactionAttributeSource;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

@EnabledIfEnvironmentVariable(named="ADAPTIVE_TEST_DB_URL",matches=".+")
class AdaptiveRepositoryIntegrationTest {
    static JdbcTemplate jdbc;static DocumentRepository documents;static AdaptiveDocumentRepository repository;
    static AdaptiveQueryTaskStore tasks;static ObjectMapper mapper=new ObjectMapper();
    @SuppressWarnings("unchecked") static <T>T transactional(T target,javax.sql.DataSource ds){
        var proxy=new ProxyFactory(target);proxy.setProxyTargetClass(true);proxy.addAdvice(new TransactionInterceptor(
                new org.springframework.jdbc.datasource.DataSourceTransactionManager(ds),new AnnotationTransactionAttributeSource()));return (T)proxy.getProxy();
    }
    @BeforeAll static void schema(){
        var ds=new DriverManagerDataSource(System.getenv("ADAPTIVE_TEST_DB_URL"),System.getenv().getOrDefault("ADAPTIVE_TEST_DB_USER","root"),System.getenv().getOrDefault("ADAPTIVE_TEST_DB_PASSWORD",""));
        new ResourceDatabasePopulator(new ClassPathResource("db/migration/V4__document_workspace.sql"),
                new ClassPathResource("db/migration/V5__document_chat.sql"),new ClassPathResource("db/migration/V6__document_pipeline.sql"),
                new ClassPathResource("db/migration/V7__document_failure_history.sql"),new ClassPathResource("db/migration/V8__document_rag_ready.sql"),
                new ClassPathResource("db/migration/V9__adaptive_document_strategies.sql"),
                new ClassPathResource("db/migration/V10__adaptive_query_diagnostics.sql"),
                new ClassPathResource("db/migration/V11__personal_knowledge_bases.sql"),
                new ClassPathResource("db/migration/V12__document_parser_audit.sql"),
                new ClassPathResource("db/migration/V15__document_structure_parents.sql")).execute(ds);
        jdbc=new JdbcTemplate(ds);documents=transactional(new DocumentRepository(jdbc),ds);
        var settings=AdaptiveStrategyTest.settings();var tokens=new TokenBudgetManager(settings);
        repository=transactional(new AdaptiveDocumentRepository(jdbc,documents,mapper,tokens,new DocumentProcessingRouter(settings)),ds);
        tasks=new AdaptiveQueryTaskStore(jdbc,mapper);
    }
    String document(){String id=UUID.randomUUID().toString();documents.create(id,7L,"fixture.pdf",1,1);documents.queue(id,"UPLOADING");documents.begin(id);return id;}
    JsonNode hierarchyFixture(String id,String version)throws Exception{
        var fixture=java.nio.file.Path.of(System.getenv().getOrDefault("ADAPTIVE_PARENT_FIXTURE","../evaluation/published-parent-fixture.json"));
        org.junit.jupiter.api.Assumptions.assumeTrue(java.nio.file.Files.isRegularFile(fixture),"External parent fixture required for this opt-in integration case");
        var payload=mapper.readTree(java.nio.file.Files.readString(fixture));
        ((com.fasterxml.jackson.databind.node.ObjectNode)payload.path("structure")).put("document_id",id).put("parse_version",version).put("batch_index",0);
        return payload;
    }
    @Test void hierarchyCommitsWithCheckpointAndReadsOnlyMatchingPublishedChild()throws Exception{
        String id=document();append(id,0,batch("source","Chapter"),0);repository.requestIndex(id);var state=repository.state(id);
        var payload=hierarchyFixture(id,state.version());repository.indexed(state,payload);
        assertEquals(1,repository.state(id).indexedBatches());assertEquals(1,repository.structureBatches(id,state.version(),-1,1).size());
        String child=payload.path("chunks").get(0).path("chunk_id").asText();
        var hit=mapper.createObjectNode().put("doc_id",id).put("index_version",state.version()).put("chunk_id",child);
        var parent=repository.parentForHit(hit);assertNotNull(parent);assertTrue(parent.path("text").asText().contains("150kg"));
        assertEquals(parent,repository.parentsForHits(List.of(hit)).get(hit.path("chunk_id").asText()));
        assertTrue(repository.parentsForHits(List.of(hit.deepCopy().put("index_version","other-version"))).isEmpty());
        assertTrue(parent.path("text").asText().contains("replacing an electric van"));
        assertNull(repository.parentForHit(hit.deepCopy().put("doc_id",UUID.randomUUID().toString())));
        assertNull(repository.parentForHit(hit.deepCopy().put("index_version","other-version")));
        assertThrows(IllegalStateException.class,()->repository.indexed(state,payload));
        assertEquals(1,repository.structureBatches(id,state.version(),-1,4).size());
    }
    @Test void invalidParentMembershipRollsBackStructureAndWatermark()throws Exception{
        String id=document();append(id,0,batch("source","Chapter"),0);var state=repository.state(id);var payload=hierarchyFixture(id,state.version());
        ((com.fasterxml.jackson.databind.node.ObjectNode)payload.path("chunks").get(0)).put("parent_id","wrong-parent");
        assertThrows(IllegalArgumentException.class,()->repository.indexed(state,payload));
        assertEquals(0,repository.state(id).indexedBatches());assertTrue(repository.structureBatches(id,state.version(),-1,4).isEmpty());
    }
    @Test void cancellationPreventsNewSourceAndIndexCommitsAndAuditRemainsQueryable()throws Exception{
        String id=document();var event=batch("original source","Chapter");append(id,0,event,0);var state=repository.state(id);
        var audit=mapper.createObjectNode().put("type","metadata").put("preflightMs",12);
        documents.parserAudit(id,audit);assertEquals(audit,mapper.readTree(documents.parserAudits(id).getFirst()));
        documents.cancelParsing(id);documents.begin(id);assertEquals("CANCELLED",documents.get(id).status());
        assertThrows(IllegalStateException.class,()->append(id,1,batch("must not commit","Chapter"),1));
        assertEquals(1,documents.get(id).parseBatches());assertEquals(1,documents.blocks(id).size());
        assertThrows(java.util.concurrent.CancellationException.class,()->repository.indexed(state,mapper.createArrayNode()));
        assertEquals(0,repository.state(id).indexedBatches());
    }
    @Test void enterprisePolicyCanDenyWritesAndKnowledgeBaseReads(){
        String id=document();
        String other=document();
        documents.authorizeScope(List.of(id,other),7L);
        assertThrows(SecurityException.class,()->documents.authorizeScope(List.of(id,other),8L));
        var defaults=new com.example.server.knowledge.KnowledgePermissionConfiguration().personalPermissionChecker();
        try{
            documents.permissionChecker((identity,resource,operation)->operation==com.example.server.knowledge.PermissionChecker.Operation.READ);
            assertNotNull(documents.owned(id,7L));
            assertThrows(SecurityException.class,()->documents.writable(id,7L));
            documents.permissionChecker((identity,resource,operation)->!resource.type().equals("KNOWLEDGE_BASE"));
            assertThrows(SecurityException.class,()->documents.owned(id,7L));
            assertThrows(SecurityException.class,()->documents.authorizeScope(List.of(id,other),7L));
        }finally{documents.permissionChecker(defaults);}
    }
    @Test void knowledgeVersionsAreScopedAndUnfinishedUpdateKeepsOldVersion()throws Exception{
        var uploads=org.mockito.Mockito.mock(DocumentUploadService.class);
        org.mockito.Mockito.when(uploads.initialize(org.mockito.ArgumentMatchers.anyString(),org.mockito.ArgumentMatchers.anyLong(),org.mockito.ArgumentMatchers.eq(7L)))
                .thenAnswer(call->{String id=UUID.randomUUID().toString();documents.create(id,7L,call.getArgument(0),call.getArgument(1),1);return id;});
        var policy=new com.example.server.knowledge.KnowledgePermissionConfiguration().personalPermissionChecker();
        var bases=transactional(new com.example.server.knowledge.KnowledgeBaseService(jdbc,policy,documents,uploads),jdbc.getDataSource());
        var base=bases.create(7L,"Version fixture","test");
        String first=bases.initialize(7L,base.id(),null,"v1.pdf",10);
        jdbc.update("UPDATE document_files SET total_blocks=1,parse_complete=TRUE,status='COMPLETED' WHERE id=?",first);
        repository.bootstrap(first,7L);
        String second=bases.initialize(7L,base.id(),first,"v2.pdf",10);
        assertEquals(List.of(first),bases.resolveScope(7L,List.of(base.id()),List.of()));
        jdbc.update("UPDATE document_files SET total_blocks=1,parse_complete=TRUE,status='COMPLETED' WHERE id=?",second);
        repository.bootstrap(second,7L);
        assertEquals(List.of(second),bases.resolveScope(7L,List.of(base.id()),List.of()));
        jdbc.update("UPDATE document_adaptive_state SET processing_strategy='STANDARD_RAG',state='INDEXING',indexed_blocks=1 WHERE document_id=?",second);
        assertEquals(List.of(first),bases.resolveScope(7L,List.of(base.id()),List.of()));
        jdbc.update("UPDATE document_adaptive_state SET state='READY' WHERE document_id=?",second);
        assertEquals(List.of(second),bases.resolveScope(7L,List.of(base.id()),List.of()));
        assertEquals(List.of(first),bases.resolveScope(7L,List.of(base.id()),List.of(first)));
        assertEquals(2,bases.versions(7L,first).size());
        assertThrows(SecurityException.class,()->bases.resolveScope(8L,List.of(base.id()),List.of(first)));
        assertThrows(IllegalArgumentException.class,()->bases.resolveScope(7L,List.of("personal-7"),List.of(first)));
        try(var pool=java.util.concurrent.Executors.newFixedThreadPool(2)){
            var a=pool.submit(()->bases.initialize(7L,base.id(),first,"v3.pdf",10));
            var b=pool.submit(()->bases.initialize(7L,base.id(),first,"v4.pdf",10));
            assertNotEquals(a.get(),b.get());
        }
        assertEquals(List.of(4,3,2,1),bases.versions(7L,first).stream().map(v->((Number)v.get("versionNumber")).intValue()).toList());
    }
    JsonNode batch(String text,String section)throws Exception{
        var event=mapper.createObjectNode();event.putArray("blocks").addObject().put("text",text).put("section",section).put("ref","#/texts/1").putArray("pages").add(1);
        return event;
    }
    void append(String id,int batch,JsonNode event,int first)throws Exception{
        repository.commitBatch(id,batch,repository.sourceUnits(event,first,"b"+batch+":"),batch+1,2,"[]",event);
    }
    @Test void shortSourcePersistenceDoesNotCreateRagChunksOrRequestIndex()throws Exception{
        String id=document();var event=batch("Redis缓存。","第一章 / 缓存");append(id,0,event,0);documents.parsed(id);repository.finish(id);
        var state=repository.state(id);assertEquals(ProcessingStrategy.DIRECT,state.strategy());assertEquals("DIRECT_READY",state.state());assertFalse(state.requested());
        assertEquals(0,jdbc.queryForObject("SELECT COUNT(*) FROM document_rag_chunks WHERE document_id=?",Integer.class,id));
        assertEquals(event.path("blocks").get(0).path("text").asText(),documents.blocks(id).getFirst().text());
        assertEquals(2,repository.sections(id).size());assertEquals(1,repository.sourceEvidence(id).size());
    }
    @Test void sectionReadsRespectVersionSectionAndContinuationCursor()throws Exception{
        String id=document();append(id,0,batch("source","Programme / Graduation"),0);var state=repository.state(id);
        var chunks=mapper.createArrayNode();
        for(int i=0;i<3;i++){var chunk=chunks.addObject().put("chunk_id",UUID.randomUUID().toString()).put("section_id",i==2?"goals":"graduation")
                .put("order",i).put("content","item"+i).put("search_text","Programme Graduation item"+i).put("token_count",4);
            chunk.putArray("sources").addObject().put("section","Programme / Graduation").put("ref","r"+i).putArray("pages").add(69);}
        repository.indexed(state,chunks);
        var first=repository.sectionEvidence(id,state.version(),List.of("graduation"),-1,1);
        assertEquals(1,first.size());assertEquals("item0",first.getFirst().path("text").asText());
        var next=repository.sectionEvidence(id,state.version(),List.of("graduation"),first.getFirst().path("blockIndex").asInt(),10);
        assertEquals(1,next.size());assertEquals("item1",next.getFirst().path("text").asText());
        assertTrue(repository.sectionEvidence(id,"other-version",List.of("graduation"),10).isEmpty());
    }
    @Test void mediumDefersIndexUntilRequestedAndRepeatedRequestsReuseState()throws Exception{
        String id=document();append(id,0,batch("a".repeat(10000),"Chapter"),0);documents.parsed(id);repository.finish(id);
        assertEquals("HYBRID_READY",repository.state(id).state());assertFalse(repository.state(id).requested());
        repository.requestIndex(id);repository.requestIndex(id);assertTrue(repository.state(id).requested());assertNotNull(repository.nextBatch(repository.state(id)));
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM document_parse_batches WHERE document_id=?",Integer.class,id));
    }
    @Test void largeAutomaticallyRequestsIncrementalIndexButCannotPublishFullBeforeParseComplete()throws Exception{
        String id=document();append(id,0,batch("a".repeat(21000),"Chapter"),0);var state=repository.state(id);
        assertEquals(ProcessingStrategy.STANDARD_RAG,state.strategy());assertTrue(state.requested());assertFalse(state.ready());
        // SQL publication contract fixture, not a claim about real vector retrieval.
        var chunks=mapper.createArrayNode();chunks.addObject().put("chunk_id",UUID.randomUUID().toString()).put("section_id","chapter").put("content","a").put("search_text","a").put("token_count",1).putArray("sources");
        repository.indexed(state,chunks);repository.publishReady(id);assertEquals("PARTIAL_READY",repository.state(id).state());
        assertThrows(IllegalStateException.class,()->repository.indexed(state,chunks));
        assertEquals(1,repository.state(id).indexedChunks());documents.parsed(id);repository.publishReady(id);assertTrue(repository.state(id).ready());
    }
    @Test void batchConflictRollsBackSourcesMetadataAndOutboxTogether()throws Exception{
        String id=document();var event=batch("first","A");append(id,0,event,0);long count=repository.state(id).tokenCount();
        assertThrows(IllegalStateException.class,()->append(id,0,event,1));
        assertEquals(count,repository.state(id).tokenCount());assertEquals(1,documents.get(id).parseBatches());
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM document_parse_batches WHERE document_id=?",Integer.class,id));
    }
    @Test void extraLargeHasRealChapterParentTreeAndNoAutomaticLlmSummary()throws Exception{
        String id=document();append(id,0,batch("a".repeat(51000),"第八章 / 性能优化"),0);
        assertEquals(ProcessingStrategy.LARGE_DOCUMENT_RAG,repository.state(id).strategy());
        var sections=repository.sections(id);assertEquals(2,sections.size());assertTrue(sections.stream().anyMatch(s->!s.path("parent_id").isNull()));
        assertNull(documents.get(id).reportJson());assertEquals(0,jdbc.queryForObject("SELECT COUNT(*) FROM document_analysis_cache",Integer.class));
    }
    @Test void multiTasksIsolateOwnersAndRejectReuseWithDifferentScopeOrQuestion()throws Exception{
        String id=UUID.randomUUID().toString();assertTrue(tasks.create(id,7L,List.of("a","b"),"比较缓存"));
        assertFalse(tasks.create(id,7L,List.of("a","b"),"比较缓存"));
        assertThrows(SecurityException.class,()->tasks.owned(id,8L));
        assertThrows(IllegalArgumentException.class,()->tasks.create(id,7L,List.of("a"),"比较缓存"));
        tasks.cancel(id);assertTrue(tasks.cancelled(id));assertFalse(tasks.begin(id));
    }
    @Test void eventReplayAndCacheArePersistentAndOwnerScoped()throws Exception{
        String id=UUID.randomUUID().toString();tasks.create(id,7L,List.of("a"),"q");tasks.event(id,"delta",Map.of("text","你好"));
        var first=tasks.events(id,0);assertEquals(1,first.size());assertTrue(tasks.events(id,((Number)first.getFirst().get("id")).longValue()).isEmpty());
        repository.cache(id,7L,mapper.readTree("{\"answer\":\"fixture\"}"));assertNotNull(repository.cached(id,7L));assertNull(repository.cached(id,8L));
    }
    @Test void bootstrapUsesSavedSourceWithoutReparsing()throws Exception{
        String id=document();documents.saveBlocks(id,List.of(new DocumentRepository.Block(0,"A","[{\"id\":\"s\",\"ref\":\"r\",\"text\":\"saved\",\"pages\":[1]}]","saved",null)),"[]");
        assertEquals("DIRECT_READY",repository.bootstrap(id,7L).state());assertEquals(5,repository.bootstrap(id,7L).tokenCount());
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM document_parse_batches WHERE document_id=?",Integer.class,id));
    }
    @Test void rebuildChangesVersionAndOldWorkerCannotAdvanceNewWatermark()throws Exception{
        String id=document();append(id,0,batch("saved text","A"),0);var previous=repository.state(id);
        repository.rebuild(id);var current=repository.state(id);assertNotEquals(previous.version(),current.version());
        assertEquals(0,current.indexedChunks());assertThrows(IllegalStateException.class,()->repository.indexed(previous,mapper.createArrayNode()));
        assertEquals(0,repository.state(id).indexedBatches());assertNotNull(repository.nextBatch(current));
    }
    @Test void upgradingPartialLegacyDocumentRetainsOriginalBatchNumbers()throws Exception{
        String id=document();var old=new DocumentRepository.Block(0,"A","[{\"id\":\"b0:s1\",\"ref\":\"b0:r\",\"text\":\"first\",\"pages\":[1]}]","first",null);
        documents.appendBatch(id,0,List.of(old),1,2,"[]");repository.bootstrap(id,7L);
        append(id,1,batch("second","B"),1);
        assertEquals(2,jdbc.queryForObject("SELECT COUNT(*) FROM document_parse_batches WHERE document_id=?",Integer.class,id));
        assertEquals(2,documents.get(id).parseBatches());assertEquals(11,repository.state(id).tokenCount());
        assertTrue(repository.nextBatch(repository.state(id)).toString().contains("first"));
    }
}
