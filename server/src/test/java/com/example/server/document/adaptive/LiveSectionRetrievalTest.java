package com.example.server.document.adaptive;
import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import java.nio.file.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.*;
/** Opt-in real BGE/Milvus evaluation. No chat model, mocked scores, or hand-picked candidate replacement. */
class LiveSectionRetrievalTest {
    boolean correct(JsonNode hit,String subject,String chapter){
        for(var source:hit.path("sources")){String section=source.path("section").asText();
            if(section.contains(subject)&&section.contains(chapter))return true;}return false;
    }
    @Test void compareFixedQuestionsAcrossEmbeddingRawRerankAndSectionAwarePipeline()throws Exception{
        String fixture=System.getenv("DOCUMENT_EVAL_FIXTURE");assumeTrue(fixture!=null,"Opt-in live retrieval evaluation requires explicit fixture");
        String casesFile=System.getenv("DOCUMENT_RETRIEVAL_CASES");assumeTrue(casesFile!=null,"External retrieval cases required for this opt-in evaluation");
        var mapper=new ObjectMapper();var scope=mapper.readTree(Files.readString(Path.of(fixture)));
        long owner=scope.path("ownerId").asLong();String doc=scope.path("documentId").asText(),version=scope.path("version").asText();
        var settings=new AdaptiveSettings(8000,20000,50000,32768,2048,1024,2000,4500,8000,12000,20,20,20,8,60,8,64,32,"http://127.0.0.1:5002","","eval");
        var client=new LocalRetrievalClient(settings,mapper);
        var state=new AdaptiveDocumentRepository.State(doc,owner,version,90000,ProcessingStrategy.LARGE_DOCUMENT_RAG,true,1,1,"READY",null);
        var sections=new ArrayList<JsonNode>();scope.path("sections").forEach(sections::add);
        var cases=mapper.readTree(Files.readString(Path.of(casesFile)));var report=mapper.createArrayNode();int passed=0;
        for(var item:cases){String q=item.path("question").asText(),subject=item.path("subject").asText(),chapter=item.path("chapter").asText();
            var unrestricted=client.retrieveTrace(owner,state,q,List.of());var raw=new ArrayList<JsonNode>();
            for(var hit:unrestricted.fused()){ObjectNode copy=hit.deepCopy();copy.remove("search_text");copy.putArray("sources");raw.add(copy);}
            var baseline=client.call("/rerank",Map.of("question",q,"candidates",raw)).path("candidates");
            String rawTop=baseline.isEmpty()?"":baseline.get(0).path("chunk_id").asText();
            boolean rawCorrect=unrestricted.fused().stream().anyMatch(h->h.path("chunk_id").asText().equals(rawTop)&&correct(h,subject,chapter));
            var plan=SectionQueryPlanner.plan(q,sections);
            var constrained=plan.ambiguous()||plan.sectionIds().isEmpty()?List.<JsonNode>of():client.retrieveTrace(owner,state,q,plan.sectionIds()).selected();
            boolean productionCorrect=!constrained.isEmpty()&&correct(constrained.getFirst(),subject,chapter);if(productionCorrect)passed++;
            var modelOnly=new ArrayList<>(unrestricted.reranked().isArray()?java.util.stream.StreamSupport.stream(unrestricted.reranked().spliterator(),false).toList():List.<JsonNode>of());
            modelOnly.sort(Comparator.comparingDouble((JsonNode hit)->hit.path("model_rerank_score").asDouble()).reversed());
            var result=report.addObject();result.put("question",q).put("denseRecallAt20",java.util.stream.StreamSupport.stream(unrestricted.dense().spliterator(),false).anyMatch(h->correct(h,subject,chapter)))
                    .put("denseTop1",!unrestricted.dense().isEmpty()&&correct(unrestricted.dense().get(0),subject,chapter))
                    .put("bm25RecallAt20",java.util.stream.StreamSupport.stream(unrestricted.sparse().spliterator(),false).anyMatch(h->correct(h,subject,chapter)))
                    .put("rrfRecallAt20",unrestricted.fused().stream().anyMatch(h->correct(h,subject,chapter)))
                    .put("rawRerankerTop1",rawCorrect).put("contextualRerankerTop1",!modelOnly.isEmpty()&&correct(modelOnly.getFirst(),subject,chapter))
                    .put("titleBoostedTop1",!unrestricted.selected().isEmpty()&&correct(unrestricted.selected().getFirst(),subject,chapter))
                    .put("sectionAwareTop1",productionCorrect).put("matchedTitlePath",plan.titlePath()).put("llmCalls",0);
            int rank=0;double reciprocal=0;for(var hit:modelOnly){rank++;if(correct(hit,subject,chapter)){reciprocal=1.0/rank;break;}}
            result.put("contextualMRRAt20",reciprocal);
        }
        Files.writeString(Path.of("target/section-retrieval-evaluation.json"),mapper.writerWithDefaultPrettyPrinter().writeValueAsString(report));
        assertEquals(cases.size(),passed,"See evaluation artifact; missing scope or wrong Top-1 must not be reported as accepted");
    }
}
