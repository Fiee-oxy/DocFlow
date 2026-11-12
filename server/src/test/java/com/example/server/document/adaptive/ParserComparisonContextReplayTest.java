package com.example.server.document.adaptive;

import com.fasterxml.jackson.databind.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import java.nio.file.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** Actual production context packer over frozen parser A/B retrieval, without LLM. */
@EnabledIfEnvironmentVariable(named="PARSER_AB_RESULTS",matches=".+")
class ParserComparisonContextReplayTest {
    @Test void packEveryFrozenTop12WithIdenticalBudgets() throws Exception {
        var mapper=new ObjectMapper();
        var tokens=new TokenBudgetManager(AdaptiveStrategyTest.settings());
        var context=new AdaptiveContextBuilder(tokens,mapper);
        Path root=Path.of(System.getenv("PARSER_AB_RESULTS"));
        int count=0;
        try(var files=Files.walk(root)) {
            for(Path file:files.filter(p->p.getFileName().toString().matches(".+-q[0-9]+(?:-paraphrase)?\\.json")).toList()) {
                var raw=mapper.readTree(file.toFile());
                if(!raw.has("ranked")) continue;
                long started=System.nanoTime();
                var actualFusion=LocalRetrievalClient.fuse(raw.path("search").path("dense"),raw.path("search").path("sparse"),60,28);
                double fusionMs=(System.nanoTime()-started)/1_000_000.0;
                assertEquals(actualFusion.stream().map(c->c.path("chunk_id").asText()).toList(),
                        java.util.stream.StreamSupport.stream(raw.path("rrf").spliterator(),false).map(c->c.path("chunk_id").asText()).toList(),
                        "Python benchmark RRF must match actual production Java fusion: "+file);
                started=System.nanoTime();
                var source=new ArrayList<JsonNode>();
                for(int i=0;i<Math.min(12,raw.path("ranked").size());i++)
                    source.addAll(context.evidence(raw.path("ranked").get(i),file.getParent().getParent().getFileName().toString()));
                // Controlled same budget across modes; no fabricated history/system token counts.
                int available=26761;
                int preferred=tokens.evidenceBudget(QueryStrategy.LOCAL_RAG,raw.path("result").path("question").asText(),available);
                int budget=context.adaptiveBudget(List.of(source),preferred,available);
                var selected=context.select(List.of(source),budget);
                assertTrue(context.cost(selected)<=budget);
                var output=mapper.createObjectNode().put("preferredBudget",preferred).put("availableBudget",available)
                        .put("evidenceBudget",budget).put("contextBytes",context.cost(selected)).put("llmCalls",0)
                        .put("contextReplayMs",(System.nanoTime()-started)/1_000_000.0)
                        .put("javaFusionMs",fusionMs)
                        .put("basis","PRODUCTION_CONTEXT_BUILDER_FIXED_BUDGET_REPLAY_NO_SECTION_EXPANSION");
                output.set("evidence",mapper.valueToTree(selected));
                mapper.writerWithDefaultPrettyPrinter().writeValue(file.resolveSibling(file.getFileName().toString().replace(".json","-context.json")).toFile(),output);
                if("true".equals(System.getenv("CHUNK_CONTEXT_BUDGET_SWEEP"))){
                    var budgets=mapper.createArrayNode();
                    for(int cap:new int[]{3000,8000,26761}){
                        int requested=tokens.evidenceBudget(QueryStrategy.LOCAL_RAG,raw.path("result").path("question").asText(),cap);
                        int actual=context.adaptiveBudget(List.of(source),requested,cap);
                        var packed=context.select(List.of(source),actual);
                        assertTrue(context.cost(packed)<=actual);
                        var item=budgets.addObject().put("availableBudget",cap).put("evidenceBudget",actual).put("cost",context.cost(packed));
                        item.set("evidence",mapper.valueToTree(packed));
                    }
                    mapper.writerWithDefaultPrettyPrinter().writeValue(file.resolveSibling(file.getFileName().toString().replace(".json","-budget-sweep.json")).toFile(),budgets);
                }
                count++;
            }
        }
        assertTrue(count>0,"No real retrieval artifacts to replay");
        mapper.writerWithDefaultPrettyPrinter().writeValue(root.resolve("context-replay-summary.json").toFile(),
                Map.of("retrievalArtifactsReplayed",count,"productionRrfVerified",true,"llmCalls",0,
                        "availableBudget",26761,"maxEvidenceBytes",20000));
    }
}
