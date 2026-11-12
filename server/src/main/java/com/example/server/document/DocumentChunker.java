package com.example.server.document;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

@Component
public class DocumentChunker {
    // Conservative character budget, independent of the video's 24k-character truncation.
    private static final int MAX_CHARS = 6_000;
    private final ObjectMapper mapper;
    private final int maxBlocks;
    public DocumentChunker(ObjectMapper mapper, @Value("${document.max-blocks:2000}") int maxBlocks) {
        this.mapper = mapper;
        this.maxBlocks = maxBlocks;
    }
    public List<DocumentRepository.Block> split(JsonNode parsed) {
        return split(parsed, 0, "");
    }
    public List<DocumentRepository.Block> split(JsonNode parsed, int firstBlockIndex, String prefix) {
        List<DocumentRepository.Block> result = new ArrayList<>();
        ArrayNode sources = mapper.createArrayNode();
        String section = "正文";
        StringBuilder text = new StringBuilder();
        int sourceIndex = 0;
        for (JsonNode item : parsed.path("blocks")) {
            String content = item.path("text").asText("").strip();
            if (content.isEmpty()) continue;
            String heading = item.path("section").asText("正文");
            if (heading.length() > 512) heading = heading.substring(0, 512);
            for (int offset = 0; offset < content.length();) {
                int end = Math.min(offset + MAX_CHARS, content.length());
                // Prefer paragraph/sentence boundaries without losing source offsets or text.
                if (end < content.length()) {
                    for (int boundary = end - 1; boundary >= offset + MAX_CHARS / 2; boundary--) {
                        if ("\n。！？.!?".indexOf(content.charAt(boundary)) >= 0) { end = boundary + 1; break; }
                    }
                }
                // Do not split a UTF-16 surrogate pair.
                if (end < content.length() && Character.isHighSurrogate(content.charAt(end - 1))) end--;
                String piece = content.substring(offset, end);
                if (!sources.isEmpty() && (!section.equals(heading) || text.length() + piece.length() > MAX_CHARS)) {
                    add(result, section, sources, text.toString(), firstBlockIndex);
                    sources = mapper.createArrayNode();
                    text.setLength(0);
                }
                section = heading;
                ObjectNode source = mapper.createObjectNode();
                source.put("id", prefix + "s" + (++sourceIndex));
                source.put("ref", prefix + item.path("ref").asText("item-" + sourceIndex));
                source.set("pages", item.path("pages").isArray() ? item.path("pages") : mapper.createArrayNode());
                source.put("offset", offset);
                source.put("label", item.path("label").asText("text"));
                source.put("text", piece);
                sources.add(source);
                text.append(piece).append('\n');
                offset = end;
            }
        }
        if (!sources.isEmpty()) add(result, section, sources, text.toString(), firstBlockIndex);
        if (result.isEmpty()) throw new IllegalArgumentException("文档没有可处理的正文");
        return result;
    }
    private void add(List<DocumentRepository.Block> blocks, String section, ArrayNode sources, String text, int offset) {
        if (blocks.size() + offset >= maxBlocks) throw new IllegalArgumentException("文档内容块数超过处理上限，请按章节拆分文档");
        blocks.add(new DocumentRepository.Block(blocks.size() + offset, section, sources.toString(), text, null));
    }
}
