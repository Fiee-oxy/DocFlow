package com.example.server.document;

import com.example.server.utils.DeepSeekUtils;
import com.example.server.service.ModelFailure;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.redisson.api.*;
import java.util.List;
import java.util.UUID;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class DocumentChatServiceTest {
    final ObjectMapper mapper = new ObjectMapper();
    final DocumentRepository repository = mock(DocumentRepository.class);
    final DeepSeekUtils model = mock(DeepSeekUtils.class);
    final RedissonClient redisson = mock(RedissonClient.class);
    final DocumentChatService service = new DocumentChatService(repository, model, mapper, redisson);
    List<DocumentRepository.Block> blocks() {
        return List.of(new DocumentRepository.Block(0, "开头", """
                [{"id":"s1","text":"本文介绍咖啡的制作方法。","pages":[1],"ref":"#/texts/1"}]
                """, "", null), new DocumentRepository.Block(1, "末尾 并发控制", """
                [{"id":"s2","text":"分布式锁用于协调多个服务实例的并发访问。","pages":[9],"ref":"#/texts/9"}]
                """, "", null));
    }
    JsonNode output() throws Exception { return mapper.readTree("""
            {"answer":"分布式锁协调并发访问。[1]","insufficientEvidence":false,
            "citations":[{"sourceId":"s2","quote":"协调多个服务实例的并发访问"}]}
            """); }
    void lock() {
        when(repository.owned("doc", 7L)).thenReturn(new DocumentRecord("doc", 7L, "book.pdf", 10, 1,
                "source", "PROCESSING", "EXTRACTING", 2, 0, 1, null, null, "[]", java.time.LocalDateTime.now()));
        RLock lock = mock(RLock.class); when(redisson.getLock(anyString())).thenReturn(lock);
        when(lock.tryLock()).thenReturn(true); when(lock.isHeldByCurrentThread()).thenReturn(true);
        RRateLimiter rate = mock(RRateLimiter.class); when(redisson.getRateLimiter(anyString())).thenReturn(rate);
        when(rate.tryAcquire()).thenReturn(true);
    }
    @Test void retrievesRelevantTailAndPreservesPages() throws Exception {
        var evidence = service.retrieve(blocks(), "分布式锁如何处理并发访问？");
        assertEquals("s2", evidence.getLast().path("id").asText());
        var response = service.validate(output(), evidence);
        assertEquals(9, response.at("/citations/0/pages/0").asInt());
        assertEquals(1, response.at("/citations/0/blockIndex").asInt());
        assertFalse(response.at("/citations/0").has("text"));
    }
    @Test void rejectsInventedQuotesAndOtherDocumentReferences() throws Exception {
        var evidence = service.retrieve(blocks(), "锁");
        var output = output(); ((com.fasterxml.jackson.databind.node.ObjectNode) output.at("/citations/0")).put("quote", "原文不存在的引用");
        assertThrows(IllegalStateException.class, () -> service.validate(output, evidence));
        ((com.fasterxml.jackson.databind.node.ObjectNode) output.at("/citations/0")).put("sourceId", "other-document");
        assertThrows(IllegalStateException.class, () -> service.validate(output, evidence));
    }
    @Test void cannotReturnFactualAnswerWithoutCitations() throws Exception {
        var output = output(); ((com.fasterxml.jackson.databind.node.ArrayNode) output.path("citations")).removeAll();
        assertThrows(IllegalStateException.class, () -> service.validate(output, service.retrieve(blocks(), "锁")));
    }
    @Test void acceptsPdfWhitespaceDifferencesButReturnsExactSourceQuote() throws Exception {
        String exact = "\u793e\u4f1a\u878d\u8d44\u89c4\u6a21\u5b58\u91cf\u540c\u6bd4\u589e\u957f 8% \uff0cM2\u540c\u6bd4\u589e\u957f\n7.3%\u3002";
        var source = mapper.createObjectNode().put("id", "s1").put("text", exact);
        source.putArray("pages").add(1);
        var generated = mapper.createObjectNode().put("answer", "8%, 7.3%").put("insufficientEvidence", false);
        generated.putArray("citations").addObject().put("sourceId", "s1")
                .put("quote", "\u793e\u4f1a\u878d\u8d44\u89c4\u6a21\u5b58\u91cf\u540c\u6bd4\u589e\u957f8%\uff0cM2\u540c\u6bd4\u589e\u957f7.3%\u3002");
        JsonNode validated = DocumentChatService.validateEvidence(generated, List.of(source), mapper);
        assertEquals(exact, validated.at("/citations/0/quote").asText());
    }
    @Test void classifiesCitationFailuresPrecisely() throws Exception {
        var unknown = output(); ((com.fasterxml.jackson.databind.node.ObjectNode) unknown.at("/citations/0")).put("sourceId", "missing");
        assertEquals(ModelFailure.Type.CITATION_SOURCE,
                assertThrows(ModelFailure.class, () -> service.validate(unknown, service.retrieve(blocks(), "锁"))).type());
        var badQuote = output(); ((com.fasterxml.jackson.databind.node.ObjectNode) badQuote.at("/citations/0")).put("quote", "不存在的原文");
        assertEquals(ModelFailure.Type.CITATION_QUOTE,
                assertThrows(ModelFailure.class, () -> service.validate(badQuote, service.retrieve(blocks(), "锁"))).type());
    }
    @Test void acceptsCaseAndPunctuationDifferencesWithoutAcceptingParaphrases() throws Exception {
        String exact = "A 405 (Method Not Allowed) response means the method is known but unsupported.";
        var source = mapper.createObjectNode().put("id", "s1").put("text", exact);
        var generated = mapper.createObjectNode().put("answer", "HTTP 405").put("insufficientEvidence", false);
        generated.putArray("citations").addObject().put("sourceId", "s1")
                .put("quote", "a 405 method not allowed response means the method is known, but unsupported");
        var validated = DocumentChatService.validateEvidence(generated, List.of(source), mapper);
        assertEquals(exact, validated.at("/citations/0/quote").asText());
        ((com.fasterxml.jackson.databind.node.ObjectNode) generated.at("/citations/0"))
                .put("quote", "The database transaction was rolled back after a deadlock");
        assertThrows(ModelFailure.class, () -> DocumentChatService.validateEvidence(generated, List.of(source), mapper));
    }
    @Test void recoversVerbatimSentenceFromGroundedParaphrase() throws Exception {
        String exact = "The origin server MUST generate an Allow header field in a 405 response.";
        var source = mapper.createObjectNode().put("id", "s1").put("text", "Unrelated preface. " + exact);
        var generated = mapper.createObjectNode().put("answer", "It must send Allow.").put("insufficientEvidence", false);
        generated.putArray("citations").addObject().put("sourceId", "s1")
                .put("quote", "The server has to generate the Allow header for a 405 response");
        var validated = DocumentChatService.validateEvidence(generated, List.of(source), mapper);
        assertEquals(exact, validated.at("/citations/0/quote").asText());
    }
    @Test void insufficientEvidenceDiscardsUnsupportedModelAnswer() throws Exception {
        var output = output(); ((com.fasterxml.jackson.databind.node.ObjectNode) output).put("insufficientEvidence", true);
        var result = service.validate(output, List.of());
        assertTrue(result.path("citations").isEmpty());
        assertTrue(result.path("answer").asText().contains("不足以回答"));
    }
    @Test void ownershipCheckedBeforeCacheAndHistory() {
        when(repository.owned("doc", 8L)).thenThrow(new SecurityException("无权访问"));
        assertThrows(SecurityException.class, () -> service.ask("doc", 8L, "问题", UUID.randomUUID().toString()));
        assertThrows(SecurityException.class, () -> service.history("doc", 8L));
        verify(repository, never()).blocks(any()); verify(repository, never()).chatHistory(any());
        verifyNoInteractions(model, redisson);
    }
    @Test void lostResponseReturnsPersistedAnswerWithoutCallingModel() throws Exception {
        lock(); var requestId = UUID.randomUUID().toString();
        when(repository.chatAnswer("doc", requestId)).thenReturn(output().toString());
        assertEquals(output(), service.ask("doc", 7L, "问题", requestId));
        verifyNoInteractions(model); verify(repository, never()).saveChat(any(), any(), any(), any());
    }
    @Test void waitsForParsingBeforeSpendingTokens() {
        lock(); when(repository.blocks("doc")).thenReturn(List.of());
        assertThrows(com.example.server.exception.BusinessException.class,
                () -> service.ask("doc", 7L, "问题", UUID.randomUUID().toString()));
        verifyNoInteractions(model);
    }
    @Test void persistsAnswerAndUsesHistoryForFollowup() throws Exception {
        lock(); when(repository.blocks("doc")).thenReturn(blocks());
        when(repository.chatHistory("doc")).thenReturn(List.of(new DocumentRepository.ChatTurn("old", "分布式锁有什么用", output().toString())));
        when(model.answerDocumentQuestion(anyString())).thenReturn(output());
        var requestId = UUID.randomUUID().toString(); var result = service.ask("doc", 7L, "它有什么作用", requestId);
        assertEquals(9, result.at("/citations/0/pages/0").asInt());
        verify(repository).saveChat(eq("doc"), eq(requestId), eq("它有什么作用"), eq(result));
        verify(model).answerDocumentQuestion(argThat(prompt -> prompt.contains("分布式锁有什么用") && prompt.contains("Evidence")));
    }
    @Test void shortResumeIncludesEveryParagraphAndTailProjectBeyondTwelveItems() throws Exception {
        var sources = mapper.createArrayNode();
        for (int i = 0; i < 40; i++) sources.addObject().put("id", "s" + i)
                .put("text", i == 39 ? "项目经历：基于RocketMQ实现视频异步处理。" : "简历段落" + i);
        var document = List.of(new DocumentRepository.Block(0, "简历", sources.toString(), "", null));
        var evidence = service.retrieve(document, "请总结这份简历");
        assertEquals(40, evidence.size());
        assertEquals("s0", evidence.getFirst().path("id").asText());
        assertTrue(evidence.getLast().path("text").asText().contains("RocketMQ"));
    }
    @Test void largeDocumentStillUsesBoundedRetrievalAndFindsRelevantTail() throws Exception {
        var sources = mapper.createArrayNode();
        for (int i = 0; i < 30; i++) sources.addObject().put("id", "s" + i)
                .put("text", i == 29 ? "分布式锁协调并发访问。" : "无关段落".repeat(400));
        var evidence = service.retrieve(List.of(new DocumentRepository.Block(0, "正文", sources.toString(), "", null)), "分布式锁");
        assertEquals("s29", evidence.getFirst().path("id").asText());
        assertTrue(evidence.size() <= 12);
        assertTrue(evidence.stream().mapToInt(s -> s.path("text").asText().length()).sum() <= 24000);
    }
}
