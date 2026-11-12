package com.example.server.service;

import com.example.server.utils.DeepSeekUtils;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class DocumentModelDiagnosticsTest {
    @Test void streamingSdkEmitsAnswerBeforeCompletionAndPreservesUsage() throws Exception {
        // Local provider-protocol fixture. This verifies streaming transport, NOT real LLM quality.
        var mapper=new ObjectMapper();var server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
        var providerFinished=new java.util.concurrent.atomic.AtomicBoolean();
        var earlyDelta=new java.util.concurrent.atomic.AtomicBoolean();var received=new java.util.concurrent.atomic.AtomicReference<com.fasterxml.jackson.databind.JsonNode>();
        server.createContext("/v1/chat/completions",exchange->{
            received.set(mapper.readTree(exchange.getRequestBody()));exchange.getResponseHeaders().set("Content-Type","text/event-stream");
            exchange.sendResponseHeaders(200,0);
            for(String piece:java.util.List.of("{\"answer\":\"你","好\",\"insufficientEvidence\":true,\"citations\":[]}")){
                var chunk=mapper.createObjectNode();chunk.put("id","fixture").put("object","chat.completion.chunk").put("model","synthetic").put("created",0);
                chunk.putArray("choices").addObject().put("index",0).putObject("delta").put("content",piece);
                exchange.getResponseBody().write(("data: "+chunk+"\n\n").getBytes(StandardCharsets.UTF_8));exchange.getResponseBody().flush();
                try{Thread.sleep(100);}catch(InterruptedException e){Thread.currentThread().interrupt();}
            }
            var finalChunk=mapper.createObjectNode();finalChunk.put("id","fixture").put("object","chat.completion.chunk").put("model","synthetic").put("created",0);
            var choice=finalChunk.putArray("choices").addObject();choice.put("index",0).put("finish_reason","stop");choice.putObject("delta");
            finalChunk.putObject("usage").put("prompt_tokens",10).put("completion_tokens",12).put("total_tokens",22);
            providerFinished.set(true);exchange.getResponseBody().write(("data: "+finalChunk+"\n\ndata: [DONE]\n\n").getBytes(StandardCharsets.UTF_8));exchange.close();
        });server.start();var executor=new ThreadPoolTaskExecutor();executor.setCorePoolSize(1);executor.initialize();
        try{
            var model=new DeepSeekUtils("synthetic-key","http://127.0.0.1:"+server.getAddress().getPort()+"/v1","synthetic",5,0,0,0,"none",mock(AgentTelemetry.class),mapper,executor);
            var text=new StringBuilder();var usage=new java.util.HashMap<String,Object>();
            var result=model.streamDocumentQuestion("protocol fixture",64,piece->{text.append(piece);if(!providerFinished.get())earlyDelta.set(true);},()->false,usage::putAll);
            assertEquals("你好",text.toString());assertEquals("你好",result.path("answer").asText());assertTrue(earlyDelta.get());
            assertTrue(received.get().path("stream").asBoolean());assertEquals(64,received.get().path("max_completion_tokens").asInt());
            assertEquals(10,usage.get("input_tokens"));assertEquals(12,usage.get("output_tokens"));
        }finally{executor.shutdown();server.stop(0);}
    }
    @Test void realSdkPathDistinguishesInvalidJsonFromHttpAuthenticationFailure() throws Exception {
        var mapper = new ObjectMapper();
        var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        int[] status = {200};
        server.createContext("/v1/chat/completions", exchange -> {
            exchange.getRequestBody().readAllBytes();
            var response = mapper.createObjectNode();
            if (status[0] == 200) {
                var choice = response.putArray("choices").addObject();
                choice.put("index", 0).put("finish_reason", "stop");
                choice.putObject("message").put("role", "assistant").put("content", "synthetic invalid JSON");
            } else response.putObject("error").put("message", "synthetic private response body");
            byte[] bytes = response.toString().getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status[0], bytes.length);
            exchange.getResponseBody().write(bytes); exchange.close();
        });
        server.start();
        var executor = new ThreadPoolTaskExecutor(); executor.setCorePoolSize(1); executor.initialize();
        try {
            var model = new DeepSeekUtils("synthetic-key", "http://127.0.0.1:" + server.getAddress().getPort() + "/v1",
                    "synthetic-model", 5, 0, 0, 0, "none", mock(AgentTelemetry.class), mapper, executor);
            var events = new ArrayList<ModelFailure>();
            try (var scope = ModelFailure.observe(events::add)) {
                var invalid = assertThrows(IllegalStateException.class, () -> model.answerDocumentQuestion("synthetic"));
                assertEquals(ModelFailure.Type.JSON_FORMAT, ModelFailure.classify(invalid).type());
            }
            assertEquals(ModelFailure.Type.JSON_FORMAT, events.getFirst().type());
            status[0] = 401; events.clear();
            try (var scope = ModelFailure.observe(events::add)) {
                var denied = assertThrows(IllegalStateException.class, () -> model.answerDocumentQuestion("synthetic"));
                assertEquals(ModelFailure.Type.API_AUTH, ModelFailure.classify(denied).type());
            }
            assertEquals(401, events.getFirst().httpStatus());
            assertFalse(events.getFirst().getMessage().contains("private"));
        } finally { executor.shutdown(); server.stop(0); }
    }
}
