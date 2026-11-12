package com.example.server.document.adaptive;
import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import java.util.*;
import static org.mockito.Mockito.*;
import static org.junit.jupiter.api.Assertions.*;

class ParentContextExpanderTest {
    ObjectMapper mapper=new ObjectMapper();
    JsonNode hit(String id)throws Exception{return mapper.readTree("{\"chunk_id\":\""+id+"\",\"doc_id\":\"d\",\"index_version\":\"v\",\"sources\":[{\"parent_id\":\"p\"}]}");}
    ObjectNode parent(){var p=mapper.createObjectNode().put("id","p").put("text","original complete context");p.putArray("child_ids").add("a").add("b");return p;}
    ParentContextExpander service(AdaptiveDocumentRepository repository){
        var s=new ParentContextExpander(repository,new TokenBudgetManager(AdaptiveStrategyTest.settings()));
        ReflectionTestUtils.setField(s,"enabled",true);return s;
    }
    @Test void disabledDoesNotReadParents()throws Exception{
        var repository=mock(AdaptiveDocumentRepository.class);var s=new ParentContextExpander(repository,new TokenBudgetManager(AdaptiveStrategyTest.settings()));
        var evidence=List.<JsonNode>of(mapper.createObjectNode().put("id","a"));
        assertSame(evidence,s.expand("why",List.of(hit("a")),evidence,"file",null));verifyNoInteractions(repository);
    }
    @Test void explanationReplacesChildrenAndPreservesWholeSource()throws Exception{
        var repository=mock(AdaptiveDocumentRepository.class);when(repository.parentsForHits(any())).thenReturn(Map.of("a",parent(),"b",parent()));
        var evidence=List.<JsonNode>of(mapper.createObjectNode().put("id","a"),mapper.createObjectNode().put("id","b"));
        var trace=mapper.createObjectNode();var result=service(repository).expand("Why did this happen?",List.of(hit("a")),evidence,"file",trace);
        assertEquals(1,result.size());assertEquals("p",result.getFirst().path("id").asText());
        assertEquals("d",result.getFirst().path("doc_id").asText());assertEquals("v",result.getFirst().path("index_version").asText());
        assertEquals("EXPLANATION",trace.path("parentExpansion").path("trigger").asText());
    }
    @Test void singleFactDoesNotExpandButMultipleChildrenDo()throws Exception{
        var repository=mock(AdaptiveDocumentRepository.class);when(repository.parentsForHits(any())).thenReturn(Map.of("a",parent(),"b",parent()));var s=service(repository);
        var evidence=List.<JsonNode>of(mapper.createObjectNode().put("id","a"));
        assertSame(evidence,s.expand("How many employees?",List.of(hit("a")),evidence,"file",null));
        assertEquals("p",s.expand("employee count",List.of(hit("a"),hit("b")),evidence,"file",null).getFirst().path("id").asText());
    }
    @Test void overBudgetOrUnrelatedChildKeepsOriginalEvidence()throws Exception{
        var repository=mock(AdaptiveDocumentRepository.class);when(repository.parentsForHits(any())).thenReturn(Map.of("a",parent(),"b",parent()));var s=service(repository);
        var evidence=List.<JsonNode>of(mapper.createObjectNode().put("id","a"));ReflectionTestUtils.setField(s,"maxBytes",1);
        assertSame(evidence,s.expand("why",List.of(hit("a")),evidence,"file",null));
        ReflectionTestUtils.setField(s,"maxBytes",6000);
        assertSame(evidence,s.expand("why",List.of(hit("outsider")),evidence,"file",null));
    }
}
