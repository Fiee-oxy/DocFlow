package com.example.server.document.adaptive;

import com.example.server.document.DocumentRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.List;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class AdaptiveRepositoryResumeStateTest {
    @Test void readsOnlyCommittedTailAndSupportsOldCheckpoints() {
        var jdbc = mock(JdbcTemplate.class);
        var repository = new AdaptiveDocumentRepository(jdbc, mock(DocumentRepository.class), new ObjectMapper(),
                mock(TokenBudgetManager.class), mock(DocumentProcessingRouter.class));
        assertNull(repository.resumeState("doc", -1));
        verifyNoInteractions(jdbc);
        String state = "{\"version\":1,\"pending\":{\"text\":\"tail\"}}";
        when(jdbc.queryForList(anyString(), eq(String.class), eq("doc"), eq(2)))
                .thenReturn(List.of(state), List.of("null"), List.of());
        assertEquals(state, repository.resumeState("doc", 2));
        assertNull(repository.resumeState("doc", 2));
        assertNull(repository.resumeState("doc", 2));
        verify(jdbc, times(3)).queryForList(contains("JSON_EXTRACT(payload,'$.docxResumeState')"), eq(String.class), eq("doc"), eq(2));
    }
    @Test void preservesDocxNodeReferencesAndEmptyPhysicalPageList() throws Exception {
        var mapper=new ObjectMapper();
        var repository=new AdaptiveDocumentRepository(mock(JdbcTemplate.class),mock(DocumentRepository.class),mapper,
                mock(TokenBudgetManager.class),mock(DocumentProcessingRouter.class));
        var event=mapper.readTree("{\"parseUnit\":\"DOCX_BODY_NODE\",\"blocks\":[{\"text\":\"Full paragraph\",\"section\":\"Chapter / Section\",\"label\":\"text\",\"ref\":\"#/texts/0\",\"node_ref\":\"docx:body:32\",\"body_node\":32,\"pages\":[],\"source_fragments\":[{\"ref\":\"docx:body:32\",\"body_node\":32,\"text\":\"Full paragraph\",\"pages\":[]}]}]}");
        var blocks=repository.sourceUnits(event,7,"b1:");
        assertEquals(7,blocks.getFirst().index());
        var source=mapper.readTree(blocks.getFirst().sourceJson()).get(0);
        assertEquals(32,source.path("body_node").asInt());
        assertEquals("docx:body:32",source.path("node_ref").asText());
        assertEquals("DOCX_BODY_NODE",source.path("parse_unit").asText());
        assertTrue(source.path("pages").isEmpty());
        assertEquals("docx:body:32",source.path("fragments").get(0).path("ref").asText());
    }
    @Test void preservesRichSourceMetadataOutsidePlainText() throws Exception {
        var mapper=new ObjectMapper();
        var repository=new AdaptiveDocumentRepository(mock(JdbcTemplate.class),mock(DocumentRepository.class),mapper,
                mock(TokenBudgetManager.class),mock(DocumentProcessingRouter.class));
        var event=mapper.readTree("""
                {"parseUnit":"DOCX_BODY_NODE","blocks":[{"text":"INVOICE TOTAL","label":"text",
                "section":"Header","ref":"#/texts/0","node_ref":"docx:part:word/header1.xml/node:0/image:a",
                "source_part":"word/header1.xml","part_kind":"header","content_kind":"image_ocr",
                "images":[{"part":"word/media/image1.png","sha256":"abc","words":[{"text":"INVOICE","confidence":97.5,"bbox":[1,2,3,4]}]}],
                "fields":[{"instruction":"PAGE","evaluation":"SAVED_RESULT_ONLY"}],"formulas":[{"ooxml":"raw"}],
                "notes":[{"part":"word/footnotes.xml","id":"2","text":"note"}],
                "nested_tables":[{"table_data":{"num_rows":1}}],"table_caption":"Table 1",
                "docx_boundary_merge":{"kind":"EXPLICIT_CONTINUATION_TABLE"},"pages":[]}]}
                """);
        var blocks=repository.sourceUnits(event,0,"b0:");
        assertEquals("INVOICE TOTAL",blocks.getFirst().text());
        var source=mapper.readTree(blocks.getFirst().sourceJson()).get(0);
        assertEquals("word/header1.xml",source.path("source_part").asText());
        assertEquals("image_ocr",source.path("content_kind").asText());
        assertEquals(97.5,source.path("images").get(0).path("words").get(0).path("confidence").asDouble());
        assertEquals("SAVED_RESULT_ONLY",source.path("fields").get(0).path("evaluation").asText());
        assertTrue(source.has("formulas"));
        assertTrue(source.has("nested_tables"));
        assertEquals("Table 1",source.path("table_caption").asText());
        assertEquals("EXPLICIT_CONTINUATION_TABLE",source.path("docx_boundary_merge").path("kind").asText());
        assertEquals("word/footnotes.xml",source.path("notes").get(0).path("part").asText());
    }
}
