package com.example.server.document;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import java.util.List;
import java.util.UUID;
import static org.junit.jupiter.api.Assertions.*;

/** Opt-in against an isolated, disposable MySQL database; no production credentials. */
@EnabledIfEnvironmentVariable(named = "DOCUMENT_TEST_DB_URL", matches = ".+")
class DocumentRepositoryIntegrationTest {
    @Test void failureHistorySurvivesSuccessAndManualRetry() throws Exception {
        String id = UUID.randomUUID().toString();
        repository.create(id, 7L, "failures.pdf", 10, 1); repository.queue(id, "UPLOADING"); repository.begin(id);
        repository.saveBlocks(id, List.of(new DocumentRepository.Block(0, "A", "[]", "source", null)), "[]");
        var failure = new com.example.server.service.ModelFailure(com.example.server.service.ModelFailure.Type.CITATION_QUOTE, "VALIDATION");
        repository.knowledgeFailure(id, 0, failure, false);
        repository.knowledgeFailed(id, 0, failure);
        assertEquals(2, repository.failures(id, 100, null).size());
        assertTrue(repository.failures(id, 100, null).getFirst().terminal());
        repository.resetFailedKnowledge(id);
        assertEquals(2, repository.failures(id, 100, null).size());
        repository.knowledge(id, 0, new ObjectMapper().readTree("{}"));
        assertTrue(repository.failures(id, 100, null).stream().allMatch(f -> f.resolvedAt() != null));
        assertEquals("CITATION_QUOTE", repository.failures(id, 1, null).getFirst().errorType());
        long cursor = repository.failures(id, 1, null).getFirst().id();
        assertEquals(1, repository.failures(id, 100, cursor).size());
        repository.knowledgeFailed(id, 0, failure);
        assertEquals(2, repository.failures(id, 100, null).size()); // no false failures after success
    }
    @Test void incrementalBatchesAreDurableAndKnowledgeCountersAreIdempotent() throws Exception {
        String id = UUID.randomUUID().toString();
        repository.create(id, 7L, "pipeline.pdf", 10, 1); repository.queue(id, "UPLOADING"); repository.begin(id);
        var first = new DocumentRepository.Block(0, "First", "[]", "first page", null);
        repository.appendBatch(id, 0, List.of(first), 1, 2, "[]");
        assertFalse(repository.get(id).parseComplete()); assertEquals(1, repository.get(id).parsedPages());
        assertTrue(repository.pendingKnowledge().stream().anyMatch(j -> j.documentId().equals(id)));
        assertThrows(IllegalStateException.class, () -> repository.appendBatch(id, 0, List.of(first), 1, 2, "[]"));
        assertEquals(1, repository.blocks(id).size());
        // Even if an insert fails after the cursor update, the transaction must restore the cursor.
        assertThrows(org.springframework.dao.DataIntegrityViolationException.class,
                () -> repository.appendBatch(id, 1, List.of(first), 2, 2, "[]"));
        assertEquals(1, repository.get(id).parseBatches());
        assertEquals(1, repository.get(id).parsedPages());
        assertEquals(1, repository.get(id).totalBlocks());
        repository.knowledge(id, 0, new ObjectMapper().readTree("{}"));
        assertFalse(repository.readyToFinalize().contains(id));
        repository.appendBatch(id, 1, List.of(new DocumentRepository.Block(1, "Last", "[]", "last page", null)), 2, 2, "[]");
        repository.parsed(id);
        assertFalse(repository.readyToFinalize().contains(id));
        try (var pool = java.util.concurrent.Executors.newFixedThreadPool(2)) {
            var json = new ObjectMapper().readTree("{}");
            var a = pool.submit(() -> repository.knowledge(id, 1, json));
            var b = pool.submit(() -> repository.knowledge(id, 1, json)); a.get(); b.get();
        }
        assertEquals(2, repository.get(id).completedBlocks());
        assertEquals("COMPLETED", repository.get(id).status()); // Parsing is enough; legacy summary counters no longer gate readiness.
        assertFalse(repository.readyToFinalize().contains(id));
    }
    private static DocumentRepository repository;
    @BeforeAll static void schema() {
        var dataSource = new DriverManagerDataSource(System.getenv("DOCUMENT_TEST_DB_URL"), "root", "");
        new ResourceDatabasePopulator(new ClassPathResource("db/migration/V4__document_workspace.sql"),
                new ClassPathResource("db/migration/V5__document_chat.sql"),
                new ClassPathResource("db/migration/V6__document_pipeline.sql"),
                new ClassPathResource("db/migration/V7__document_failure_history.sql")).execute(dataSource);
        var proxy = new org.springframework.aop.framework.ProxyFactory(new DocumentRepository(new JdbcTemplate(dataSource)));
        proxy.setProxyTargetClass(true);
        proxy.addAdvice(new org.springframework.transaction.interceptor.TransactionInterceptor(
                new org.springframework.jdbc.datasource.DataSourceTransactionManager(dataSource),
                new org.springframework.transaction.annotation.AnnotationTransactionAttributeSource()));
        repository = (DocumentRepository) proxy.getProxy();
    }
    @Test void persistsUploadReceiptBlocksKnowledgeReportAndOwnership() throws Exception {
        String id = UUID.randomUUID().toString();
        var mapper = new ObjectMapper();
        repository.create(id, 7L, "book.docx", 10, 1);
        repository.part(id, 0, 10);
        repository.part(id, 0, 10);
        assertEquals(List.of(0), repository.parts(id));
        assertThrows(SecurityException.class, () -> repository.owned(id, 8L));
        assertTrue(repository.queue(id, "UPLOADING"));
        assertFalse(repository.queue(id, "UPLOADING"));
        repository.begin(id);
        repository.source(id, "http://minio/source.docx", "hash");
        repository.saveBlocks(id, List.of(new DocumentRepository.Block(0, "章节", "[]", "正文", null)), "[]");
        repository.knowledge(id, 0, mapper.readTree("{\"knowledgePoints\":[]}"));
        repository.knowledge(id, 0, mapper.readTree("{\"knowledgePoints\":[]}"));
        assertEquals(1, repository.get(id).completedBlocks());
        assertEquals(1, repository.get(id).attempts());
        repository.complete(id, mapper.readTree("{\"title\":\"报告\"}"));
        assertEquals("COMPLETED", repository.owned(id, 7L).status());
        assertEquals("报告", mapper.readTree(repository.get(id).reportJson()).path("title").asText());
        assertTrue(repository.list(7L).stream().anyMatch(doc -> doc.id().equals(id)));
        assertTrue(repository.list(8L).isEmpty());
    }
    @Test void partialFailureRetainsSuccessfulBlocksForManualRetry() throws Exception {
        String id = UUID.randomUUID().toString();
        repository.create(id, 9L, "retry.pdf", 10, 1);
        repository.queue(id, "UPLOADING");
        repository.begin(id);
        repository.saveBlocks(id, List.of(
                new DocumentRepository.Block(0, "一", "[]", "已处理", null),
                new DocumentRepository.Block(1, "二", "[]", "待处理", null)), "[]");
        repository.knowledge(id, 0, new ObjectMapper().readTree("{}"));
        repository.failed(id, "temporary failure", false);
        repository.queue(id, "FAILED");
        assertEquals("QUEUED", repository.get(id).status());
        assertNotNull(repository.blocks(id).getFirst().knowledgeJson());
        assertNull(repository.blocks(id).getLast().knowledgeJson());
    }
    @Test void chatPersistsInOrderAndRequestIdsAreUniqueWithinDocument() throws Exception {
        String id = UUID.randomUUID().toString(), other = UUID.randomUUID().toString();
        repository.create(id, 7L, "chat.pdf", 10, 1);
        repository.create(other, 8L, "other.pdf", 10, 1);
        var response = new ObjectMapper().readTree("{\"answer\":\"有原文依据的回答\",\"citations\":[]}");
        for (int i = 0; i < 35; i++) repository.saveChat(id, "request-" + i, "问题" + i, response);
        assertEquals(30, repository.chatHistory(id).size());
        assertEquals("问题5", repository.chatHistory(id).getFirst().question());
        assertEquals("问题34", repository.chatHistory(id).getLast().question());
        assertEquals(response.toString(), repository.chatAnswer(id, "request-0"));
        assertNull(repository.chatAnswer(other, "request-0"));
        assertThrows(org.springframework.dao.DuplicateKeyException.class,
                () -> repository.saveChat(id, "request-0", "重复请求", response));
    }
}
