package com.example.server.document.adaptive;
import com.example.server.document.DocumentRecord;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import java.time.LocalDateTime;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
class StructuredDocumentQueryTest {
    StructuredDocumentQuery service(boolean enabled){var p=new RetrievalRoutingProperties();p.setEnabled(enabled);return new StructuredDocumentQuery(p,new ObjectMapper());}
    DocumentRecord doc(){return new DocumentRecord("d",7L,"report.pdf",123,1,null,"UPLOADING","UPLOAD",0,0,0,null,null,"[]",LocalDateTime.of(2026,11,5,12,0));}
    @Test void readsOwnedRecordWithoutAnIndexOrModel(){
        var answer=service(true).answer("文件大小是多少？",List.of(doc()),Arrays.asList((AdaptiveDocumentRepository.State)null));
        assertEquals(0,answer.path("llmCalls").asInt());assertEquals("STRUCTURED",answer.path("retrievalMode").asText());assertTrue(answer.path("answer").asText().contains("123 bytes"));
    }
    @Test void contentQuestionsAndDisabledModeNeverUseMetadataShortcut(){
        var service=service(true);assertFalse(service.supported("文档中提到的文件大小限制是多少？"));assertFalse(service.supported("解释上传时间如何影响性能"));
        assertFalse(service(false).supported("文件数量"));assertTrue(service.supported("解析状态"));
    }
}
