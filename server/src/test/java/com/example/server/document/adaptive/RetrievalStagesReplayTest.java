package com.example.server.document.adaptive;

import com.fasterxml.jackson.databind.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Assumptions;
import java.nio.file.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** Explicit A-component replay. Parent resolver matches production default disabled; no fake DB timing. */
class RetrievalStagesReplayTest {
    @Test void replaySameHttpCandidatesThroughJavaComponents()throws Exception{
        String input=System.getenv("RETRIEVAL_STAGE_REPLAY");Assumptions.assumeTrue(input!=null&&!input.isBlank());
        var mapper=new ObjectMapper();var root=mapper.readTree(Files.readString(Path.of(input)));var output=mapper.createArrayNode();
        var tokens=new TokenBudgetManager(AdaptiveStrategyTest.settings());var context=new AdaptiveContextBuilder(tokens,mapper);
        var parents=new ParentContextExpander(null,tokens); // Default disabled, therefore no database access.
        for(var record:root.path("records")){
            if(!record.path("result").path("ok").asBoolean())continue;
            var hits=new ArrayList<JsonNode>();record.path("result").path("data").path("selected").forEach(hits::add);
            var evidence=new ArrayList<JsonNode>();for(var hit:hits)evidence.addAll(context.evidence(hit,"stage-A-document"));
            var trace=mapper.createObjectNode();evidence=new ArrayList<>(parents.expand(record.path("question").asText(),hits,evidence,"stage-A-document",trace));
            long start=System.nanoTime();var selected=context.select(List.of(evidence),8000);long cost=context.cost(selected);double elapsed=(System.nanoTime()-start)/1_000_000.0;
            assertTrue(cost<=8000);
            var row=output.addObject().put("requestId",record.path("requestId").asText()).put("id",record.path("id").asText()).put("contextBuilderMs",elapsed)
                .put("parentResolverMs",trace.path("stageTimingMs").path("parentResolver").asDouble()).put("parentResolverEnabled",false)
                .put("contextBudgetBytes",8000).put("contextBytesEstimated",cost).put("selectedSources",selected.size());
            row.put("scope","ACTUAL_JAVA_COMPONENT_REPLAY_NOT_FULL_SPRING_HTTP_PIPELINE");
        }
        assertEquals(6,output.size());Files.writeString(Path.of(input).resolveSibling("A-java-stages.json"),mapper.writerWithDefaultPrettyPrinter().writeValueAsString(output));
    }
}
