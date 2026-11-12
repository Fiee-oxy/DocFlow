package com.example.server.document;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class DocumentChunkerTest {
    private final ObjectMapper mapper = new ObjectMapper();
    @Test void splitsLongParagraphWithoutDroppingTailOrLosingSource() throws Exception {
        var parsed = mapper.createObjectNode();
        parsed.putArray("blocks").addObject().put("section", "正文").put("ref", "#/texts/7")
                .put("text", "中".repeat(12001));
        var blocks = new DocumentChunker(mapper, 10).split(parsed);
        assertEquals(3, blocks.size());
        assertEquals(12001, blocks.stream().mapToInt(b -> b.text().strip().length()).sum());
        assertEquals(12000, mapper.readTree(blocks.get(2).sourceJson()).get(0).path("offset").asInt());
    }
    @Test void keepsSectionBoundariesAndRejectsOverBudgetRatherThanTruncating() throws Exception {
        var parsed = mapper.readTree("""
                {"blocks":[{"section":"一","text":"正文一"},{"section":"二","text":"末尾内容"}]}
                """);
        assertEquals(2, new DocumentChunker(mapper, 2).split(parsed).size());
        assertThrows(IllegalArgumentException.class, () -> new DocumentChunker(mapper, 1).split(parsed));
    }
}
