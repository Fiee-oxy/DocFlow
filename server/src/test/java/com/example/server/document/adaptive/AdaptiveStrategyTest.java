package com.example.server.document.adaptive;

import com.fasterxml.jackson.databind.*;
import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class AdaptiveStrategyTest {
    static AdaptiveSettings settings(){return new AdaptiveSettings(8000,20000,50000,32768,2048,1024,2000,4500,8000,12000,
            20,20,20,8,60,8,64,32,"","","hybrid-v1-bge-small-zh-480");}
    final ObjectMapper mapper=new ObjectMapper();
    final TokenBudgetManager tokens=new TokenBudgetManager(settings());
    final QueryStrategyRouter query=new QueryStrategyRouter();
    @Test void sourceCoordinatesCannotCrowdOutRequiredNumbers()throws Exception{
        var context=new AdaptiveContextBuilder(tokens,mapper);
        var intro=mapper.createObjectNode().put("id","intro").put("doc_id","a").put("text","A manually annotated evaluation corpus.");
        var facts=mapper.createObjectNode().put("id","facts").put("doc_id","a").put("text","The test dataset contains 53453 tokens and 2566 sentences.");
        facts.put("coordinates","x".repeat(10000));
        var candidates=List.of(List.<JsonNode>of(intro,facts));
        int budget=context.adaptiveBudget(candidates,100,2000);
        var selected=context.select(candidates,budget);
        assertEquals(2,selected.size());assertTrue(context.cost(selected)<=budget);
        assertFalse(mapper.writeValueAsString(context.promptEvidence(selected)).contains("coordinates"));
        assertEquals(10000,selected.getLast().path("coordinates").asText().length());
    }
    @Test void channelProtectionKeepsUniqueLexicalEvidenceWhenSharedHitsFillFusion()throws Exception{
        var dense=mapper.createArrayNode();var sparse=mapper.createArrayNode();
        for(int i=0;i<10;i++)dense.addObject().put("chunk_id","shared"+i);
        sparse.addObject().put("chunk_id","unique-answer");
        for(int i=0;i<10;i++)sparse.addObject().put("chunk_id","shared"+i);
        var fused=LocalRetrievalClient.fuse(dense,sparse,60,6);
        assertEquals(6,fused.size());assertTrue(fused.stream().anyMatch(h->h.path("chunk_id").asText().equals("unique-answer")));
        assertTrue(fused.stream().allMatch(h->h.has("rrf_score")));
    }
    @Test void enumerativeQuestionPreservesMultipleEvidenceBlocksWithinAvailableBudget()throws Exception{
        var sources=new ArrayList<JsonNode>();
        for(int i=0;i<8;i++){var source=mapper.createObjectNode();source.put("doc_id","curriculum").put("id","s"+i)
                .put("text","工程知识与问题分析。".repeat(45)+i);sources.add(source);}
        var context=new AdaptiveContextBuilder(tokens,mapper);
        int budget=tokens.evidenceBudget(QueryStrategy.LOCAL_RAG,"大数据专业毕业要求",20000);
        assertTrue(context.select(List.of(sources),budget).size()>1);
        assertTrue(tokens.count(mapper.writeValueAsString(context.select(List.of(sources),budget)))<=budget);
        assertEquals(1800,tokens.evidenceBudget(QueryStrategy.LOCAL_RAG,"大数据专业毕业要求",1800));
        assertEquals(2000,tokens.evidenceBudget(QueryStrategy.LOCAL_RAG,"Redis的定义是什么",20000));
    }
    QueryStrategyRouter.QueryRoutingContext ctx(String q,List<String> ids,long count,ProcessingStrategy strategy,boolean ready,boolean partial,int available,boolean complete){
        return new QueryStrategyRouter.QueryRoutingContext(q,ids,count,strategy,ready,partial,available,complete);
    }
    @Test void processingThresholdsAreIndependentOfFilePagesAndBytes(){
        var router=new DocumentProcessingRouter(settings());
        assertEquals(ProcessingStrategy.DIRECT,router.route(8000));
        assertEquals(ProcessingStrategy.HYBRID_DECISION,router.route(8001));
        assertEquals(ProcessingStrategy.HYBRID_DECISION,router.route(20000));
        assertEquals(ProcessingStrategy.STANDARD_RAG,router.route(20001));
        assertEquals(ProcessingStrategy.LARGE_DOCUMENT_RAG,router.route(50001));
        assertThrows(IllegalArgumentException.class,()->router.route(-1));
    }
    @Test void shortDocumentNeedsNoIndexButReevaluatesWhenHistoryExhaustsBudget(){
        assertEquals(QueryStrategy.DIRECT_CONTEXT,query.route(ctx("内容是什么",List.of("a"),7000,ProcessingStrategy.DIRECT,false,false,9000,true)).strategy());
        var afterHistory=query.route(ctx("内容是什么",List.of("a"),7000,ProcessingStrategy.DIRECT,false,false,6000,true));
        assertEquals(QueryStrategy.LOCAL_RAG,afterHistory.strategy());assertTrue(afterHistory.needsIndex());
    }
    @Test void mediumDocumentSwitchesBetweenDirectAndReadyRag(){
        var without=query.route(ctx("什么是缓存",List.of("a"),10000,ProcessingStrategy.HYBRID_DECISION,false,false,16000,true));
        assertEquals(QueryStrategy.DIRECT_CONTEXT,without.strategy());assertTrue(without.needsIndex());
        assertEquals(QueryStrategy.LOCAL_RAG,query.route(ctx("什么是缓存",List.of("a"),10000,ProcessingStrategy.HYBRID_DECISION,true,true,16000,true)).strategy());
        assertEquals("WAIT_FOR_INDEX",query.route(ctx("什么是缓存",List.of("a"),10000,ProcessingStrategy.HYBRID_DECISION,false,false,9000,true)).reason());
    }
    @Test void largeDocumentDoesNotDefaultToDirectDespiteHugeModelWindow(){
        assertEquals(QueryStrategy.LOCAL_RAG,query.route(ctx("缓存如何工作",List.of("a"),30000,ProcessingStrategy.STANDARD_RAG,true,true,100000,true)).strategy());
    }
    @Test void extraLargeRoutesSectionsAndGlobalBeforeLocal(){
        assertEquals(QueryStrategy.SECTION_RAG,query.route(ctx("第八章介绍什么",List.of("a"),90000,ProcessingStrategy.LARGE_DOCUMENT_RAG,false,true,20000,false)).strategy());
        assertEquals(QueryStrategy.GLOBAL_ANALYSIS,query.route(ctx("总结全文",List.of("a"),90000,ProcessingStrategy.LARGE_DOCUMENT_RAG,false,true,20000,false)).strategy());
        assertEquals("EXPLICIT_PARTIAL_COVERAGE",query.route(ctx("缓存作用",List.of("a"),90000,ProcessingStrategy.LARGE_DOCUMENT_RAG,false,true,20000,false)).reason());
    }
    @Test void multiDocumentHasHighestPriorityAndCannotMasqueradeAsSingleGlobal(){
        assertEquals(QueryStrategy.MULTI_DOCUMENT,query.route(ctx("总结全部文件",List.of("a","b"),500,ProcessingStrategy.DIRECT,true,true,20000,true)).strategy());
    }
    @Test void budgetsReservePromptHistoryOutputAndSafety(){
        assertEquals(32768-2048-1024-3-1-2,tokens.available("中","a","[]"));
        assertThrows(IllegalArgumentException.class,()->tokens.available("a".repeat(32768),"q","[]"));
        assertEquals(2000,tokens.evidenceBudget(QueryStrategy.LOCAL_RAG,"缓存是什么",10000));
        assertEquals(300,tokens.evidenceBudget(QueryStrategy.LOCAL_RAG,"解释缓存",300));
        assertEquals("UTF8_BYTE_UPPER_BOUND",tokens.countingMode());
    }
    @Test void rrfDeduplicatesAcrossChannelsAndPromotesSharedHit()throws Exception{
        var dense=mapper.readTree("[{\"chunk_id\":\"a\"},{\"chunk_id\":\"b\"}]");
        var sparse=mapper.readTree("[{\"chunk_id\":\"c\"},{\"chunk_id\":\"b\"},{\"chunk_id\":\"b\"}]");
        var fused=LocalRetrievalClient.fuse(dense,sparse,60,20);
        assertEquals(3,fused.size());assertEquals("b",fused.getFirst().path("chunk_id").asText());
    }
    @Test void contextBudgetGivesEachDocumentAnOpportunityWithoutDuplicatingText()throws Exception{
        var a=mapper.readTree("{\"id\":\"a\",\"doc_id\":\"a\",\"text\":\"Redis缓存\"}");
        var b=mapper.readTree("{\"id\":\"b\",\"doc_id\":\"b\",\"text\":\"MySQL事务\"}");
        var result=new AdaptiveContextBuilder(tokens,mapper).select(List.of(List.of(a,a),List.of(b)),500);
        assertEquals(2,result.size());assertEquals("b",result.get(1).path("doc_id").asText());
    }
    @Test void globalPartitionCoversEveryCharacterIncludingOversizedSource()throws Exception{
        var source=mapper.createObjectNode();source.put("id","a");source.put("text","第一章😀".repeat(200));
        var groups=new AdaptiveContextBuilder(tokens,mapper).partition(List.of(source),500);
        String reconstructed=groups.stream().flatMap(List::stream).map(n->n.path("text").asText()).reduce("",String::concat);
        assertEquals(source.path("text").asText(),reconstructed);
        for(var group:groups)assertTrue(tokens.count(mapper.writeValueAsString(group))<=500);
    }
    @Test void jsonStreamDecoderWaitsForSplitEscapesAndEmitsOnlyAnswer()throws Exception{
        var decoder=new JsonAnswerDeltaDecoder(mapper);
        assertEquals("",decoder.accept("{\"ans"));
        assertEquals("你好",decoder.accept("wer\":\"你好\\u"));
        assertEquals("中\n",decoder.accept("4e2d\\n"));
        assertEquals("",decoder.accept("\",\"citations\":[]}"));
    }
}
