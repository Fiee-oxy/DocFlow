package com.example.server.document.adaptive;

import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import java.nio.file.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** Replay frozen retrieval results to isolate context packing from model/retrieval changes. */
@EnabledIfEnvironmentVariable(named="RAG_CONTEXT_REPLAY_DIR",matches=".+")
class ContextBudgetReplayTest {
    @Test void replayWithoutAnyNetworkOrModelCall()throws Exception{
        var mapper=new ObjectMapper();var context=new AdaptiveContextBuilder(new TokenBudgetManager(AdaptiveStrategyTest.settings()),mapper);
        Path original=Path.of(System.getenv("RAG_CONTEXT_REPLAY_DIR"));Path output=Path.of(System.getenv("RAG_CONTEXT_REPLAY_OUTPUT"));
        Files.createDirectories(output.resolve("previews"));
        try(var files=Files.list(original.resolve("previews"))){
            for(Path file:files.filter(p->p.toString().endsWith(".json")).sorted().toList()){
                var raw=(ObjectNode)mapper.readTree(file.toFile());var perDocument=new ArrayList<List<JsonNode>>();
                boolean replayable=true;
                for(var trace:raw.path("retrieval")){
                    var sources=new ArrayList<JsonNode>();
                    if(trace.path("sectionExpansion").asBoolean())for(var batch:trace.path("sectionReadPlan"))batch.path("sources").forEach(sources::add);
                    else if(trace.path("path").asText().equals("HYBRID_RAG"))for(var hit:trace.path("topK"))sources.addAll(context.evidence(hit,trace.path("filename").asText()));
                    else replayable=false;
                    perDocument.add(sources);
                }
                if(replayable&&!perDocument.isEmpty()){
                    int budget=context.adaptiveBudget(perDocument,raw.path("evidenceBudget").asInt(),raw.path("availableContextBudget").asInt());
                    var selected=context.select(perDocument,budget);assertTrue(context.cost(selected)<=budget);
                    raw.set("evidence",mapper.valueToTree(selected));raw.put("selectedSourceCount",selected.size());
                    raw.put("evidenceBudget",budget).put("contextTokensEstimated",context.cost(selected));
                }
                raw.put("contextReplayOnly",true).put("retrievalAndRerankerFrozen",true).put("llmCalls",0);
                mapper.writerWithDefaultPrettyPrinter().writeValue(output.resolve("previews").resolve(file.getFileName()).toFile(),raw);
            }
        }
    }
}
