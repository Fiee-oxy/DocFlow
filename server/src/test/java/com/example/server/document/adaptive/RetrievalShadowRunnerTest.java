package com.example.server.document.adaptive;
import com.example.server.document.DocumentRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import java.net.InetSocketAddress;
import java.util.*;
import java.util.concurrent.*;
import static org.mockito.Mockito.*;
import static org.junit.jupiter.api.Assertions.*;

class RetrievalShadowRunnerTest {
    @Test void boundedShadowReturnsImmediatelyAndReauthorizes()throws Exception{
        var server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);var arrived=new CountDownLatch(1);var release=new CountDownLatch(1);
        server.createContext("/retrieve-routed",exchange->{arrived.countDown();try{release.await(5,TimeUnit.SECONDS);}catch(InterruptedException e){Thread.currentThread().interrupt();}
            byte[] data="{\"selected\":[],\"timingMs\":{\"total\":1}}".getBytes();exchange.sendResponseHeaders(200,data.length);exchange.getResponseBody().write(data);exchange.close();});server.start();
        var p=new RetrievalRoutingProperties();p.setEnabled(true);p.setMode(RetrievalRoutingProperties.RunMode.SHADOW);p.getShadow().setUrl("http://127.0.0.1:"+server.getAddress().getPort());p.getShadow().setSampleRate(1);p.getShadow().setQueueCapacity(1);p.validate();
        var docs=mock(DocumentRepository.class);var runner=new RetrievalShadowRunner(p,docs,new ObjectMapper());runner.initialize();
        var state=new AdaptiveDocumentRepository.State("d",7L,"v",100,ProcessingStrategy.LARGE_DOCUMENT_RAG,true,1,1,"READY",null);
        try{
            assertTimeout(java.time.Duration.ofSeconds(1),()->runner.submit(7L,state,"query",List.of(),"query",true,List.of(),AdaptiveStrategyTest.settings()));
            assertTrue(arrived.await(5,TimeUnit.SECONDS));
            runner.submit(7L,state,"query",List.of(),"query",true,List.of(),AdaptiveStrategyTest.settings());runner.submit(7L,state,"query",List.of(),"query",true,List.of(),AdaptiveStrategyTest.settings());
            assertTrue(runner.counters().get("dropped")>=1);verify(docs).owned("d",7L);release.countDown();
            long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(5);
            while(runner.observations(7L).isEmpty()&&System.nanoTime()<deadline)Thread.sleep(10);
            assertFalse(runner.observations(7L).isEmpty());assertTrue(runner.observations(8L).isEmpty());
            doThrow(new SecurityException("revoked")).when(docs).owned("d",7L);assertTrue(runner.observations(7L).isEmpty());
        }finally{release.countDown();runner.close();server.stop(0);}
    }
}
