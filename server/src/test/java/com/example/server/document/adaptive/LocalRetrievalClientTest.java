package com.example.server.document.adaptive;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import java.net.InetSocketAddress;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.jupiter.api.Assertions.*;
class LocalRetrievalClientTest {
    @Test void configuredConfidenceGateIsSentWithRoutedRequest()throws Exception{
        var mapper=new ObjectMapper();var client=new LocalRetrievalClient(AdaptiveStrategyTest.settings(),mapper){
            @Override public com.fasterxml.jackson.databind.JsonNode call(String path,Object payload)throws Exception{
                var body=(Map<?,?>)payload;assertEquals(true,body.get("fallback_enabled"));assertEquals(false,body.get("fallback_on_empty"));
                assertEquals(true,body.get("fallback_on_low_confidence"));assertEquals(.6,body.get("dense_min_score"));
                return mapper.readTree("{\"dense\":[],\"sparse\":[],\"fused\":[],\"reranked\":[],\"selected\":[],\"routing\":{\"revision\":\"retrieval-router-v2\"}}");
            }
        };
        var properties=new RetrievalRoutingProperties();properties.setEnabled(true);properties.getFallback().setOnEmpty(false);properties.getFallback().setDenseMinScore(.6);
        org.springframework.test.util.ReflectionTestUtils.setField(client,"routingProperties",properties);
        client.retrieveTrace(7L,new AdaptiveDocumentRepository.State("doc",7L,"v",100,ProcessingStrategy.STANDARD_RAG,true,1,1,"READY",null),"query",java.util.List.of());
    }
    @Test void routedRetrievalPreservesScopeAndReturnsDecision()throws Exception{
        var mapper=new ObjectMapper();
        var client=new LocalRetrievalClient(AdaptiveStrategyTest.settings(),mapper){
            @Override public com.fasterxml.jackson.databind.JsonNode call(String path,Object payload)throws Exception{
                assertEquals("/retrieve-routed",path);
                assertEquals(7L,((Map<?,?>)payload).get("owner_id"));
                return mapper.readTree("""
                    {"dense":[],"sparse":[],"fused":[],"reranked":[],"selected":[],
                     "routing":{"revision":"retrieval-router-v2","fallback":true},"timingMs":{"total":10}}
                    """);
            }
        };
        org.springframework.test.util.ReflectionTestUtils.setField(client,"routingEnabled",true);
        var state=new AdaptiveDocumentRepository.State("doc",7L,"v",100,ProcessingStrategy.LARGE_DOCUMENT_RAG,true,1,1,"READY",null);
        var trace=client.retrieveTrace(7L,state,"question",java.util.List.of());
        assertTrue(trace.routing().path("fallback").asBoolean());assertEquals(10,trace.routing().path("timingMs").path("total").asInt());
    }
    @Test void routedResultsOutsideVersionAreRejected()throws Exception{
        var mapper=new ObjectMapper();
        var client=new LocalRetrievalClient(AdaptiveStrategyTest.settings(),mapper){
            @Override public com.fasterxml.jackson.databind.JsonNode call(String path,Object payload)throws Exception{
                return mapper.readTree("""
                    {"dense":[{"doc_id":"doc","index_version":"other"}],"sparse":[],"fused":[],"reranked":[],"selected":[]}
                    """);
            }
        };
        org.springframework.test.util.ReflectionTestUtils.setField(client,"routingEnabled",true);
        var state=new AdaptiveDocumentRepository.State("doc",7L,"v",100,ProcessingStrategy.LARGE_DOCUMENT_RAG,true,1,1,"READY",null);
        assertThrows(IllegalStateException.class,()->client.retrieveTrace(7L,state,"question",java.util.List.of()));
    }
    @Test void expansionIdentityIncludesConfigurationButNotQuestion()throws Exception{
        var mapper=new ObjectMapper();
        var client=new LocalRetrievalClient(AdaptiveStrategyTest.settings(),mapper){
            @Override public com.fasterxml.jackson.databind.JsonNode call(String path,Object payload)throws Exception{
                assertEquals("/query/expand",path);
                return mapper.readTree("""
                    {"policy_version":"query-alias-v1","enabled":true,"dictionary_version":"abc",
                     "route_weight":0.25,"max_terms":6,"original_query":"private question","terms":["alias"]}
                    """);
            }
        };
        var policy=client.expansionPolicy("question");
        assertEquals(7,policy.size());assertEquals("abc",policy.path("dictionary_version").asText());
        assertFalse(policy.path("retrievalRouterEnabled").asBoolean());
        assertFalse(policy.has("original_query"));assertFalse(policy.has("terms"));
        var changed=(com.fasterxml.jackson.databind.node.ObjectNode)policy.deepCopy();
        changed.put("dictionary_version","changed");assertNotEquals(policy,changed);
    }
    @Test void missingExpansionVersionCannotReuseAnswerCache(){
        var mapper=new ObjectMapper();
        var client=new LocalRetrievalClient(AdaptiveStrategyTest.settings(),mapper){
            @Override public com.fasterxml.jackson.databind.JsonNode call(String path,Object payload){return mapper.createObjectNode();}
        };
        assertThrows(IllegalStateException.class,()->client.expansionPolicy("question"));
    }
    @Test void productionRejectsExperimentalStrategy(){
        var client=new LocalRetrievalClient(AdaptiveStrategyTest.settings(),new ObjectMapper());
        assertDoesNotThrow(client::validateChunkConfiguration);
        org.springframework.test.util.ReflectionTestUtils.setField(client,"chunkStrategy","boundary");
        assertThrows(IllegalArgumentException.class,client::validateChunkConfiguration);
    }
    @Test void retriesOnlyTemporaryInferenceContention()throws Exception{
        var server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);var attempts=new AtomicInteger();
        server.createContext("/search",exchange->{int code=attempts.incrementAndGet()==1?429:200;byte[] body="{\"dense\":[]}".getBytes();
            exchange.sendResponseHeaders(code,body.length);exchange.getResponseBody().write(body);exchange.close();});server.start();
        try{var defaults=AdaptiveStrategyTest.settings();var settings=new AdaptiveSettings(8000,20000,50000,32768,2048,1024,2000,4500,8000,12000,20,20,20,8,60,8,64,32,
                    "http://127.0.0.1:"+server.getAddress().getPort(),"","test");
            assertTrue(new LocalRetrievalClient(settings,new ObjectMapper()).call("/search",Map.of()).has("dense"));assertEquals(2,attempts.get());
        }finally{server.stop(0);}
    }
    @Test void authenticationFailureIsNotRetried()throws Exception{
        var server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);var attempts=new AtomicInteger();
        server.createContext("/search",exchange->{attempts.incrementAndGet();exchange.sendResponseHeaders(401,-1);exchange.close();});server.start();
        try{var settings=new AdaptiveSettings(8000,20000,50000,32768,2048,1024,2000,4500,8000,12000,20,20,20,8,60,8,64,32,
                    "http://127.0.0.1:"+server.getAddress().getPort(),"","test");
            assertThrows(com.example.server.exception.BusinessException.class,()->new LocalRetrievalClient(settings,new ObjectMapper()).call("/search",Map.of()));assertEquals(1,attempts.get());
        }finally{server.stop(0);}
    }
}
