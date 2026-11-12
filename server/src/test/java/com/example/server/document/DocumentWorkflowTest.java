package com.example.server.document;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.redisson.api.*;
import java.time.LocalDateTime;
import java.util.List;
import java.util.concurrent.TimeUnit;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class DocumentWorkflowTest {
    @org.junit.jupiter.api.io.TempDir java.nio.file.Path directory;
    private static final String ID = "91443c93-7f4b-43f8-91c0-22601ac83341";
    private final ObjectMapper mapper = new ObjectMapper();
    private final DocumentRepository repository = mock(DocumentRepository.class);
    private final DocumentUploadService uploads = mock(DocumentUploadService.class);
    private final DoclingClient parser = mock(DoclingClient.class);
    private final DocumentChunker chunker = mock(DocumentChunker.class);
    private final RedissonClient redisson = mock(RedissonClient.class);
    private final RLock lock = mock(RLock.class);

    private DocumentWorkflow service(String status, int attempts) {
        when(repository.get(ID)).thenReturn(new DocumentRecord(ID, 7L, "book.pdf", 10, 1, "source", status,
                "EXTRACTING", 2, 1, attempts, null, null, "[]", LocalDateTime.now()));
        when(redisson.getLock(anyString())).thenReturn(lock);
        when(lock.tryLock()).thenReturn(true);
        when(lock.isHeldByCurrentThread()).thenReturn(true);
        RRateLimiter limiter = mock(RRateLimiter.class);
        when(redisson.getRateLimiter(anyString())).thenReturn(limiter);
        when(limiter.tryAcquire(eq(1L), eq(60L), eq(TimeUnit.SECONDS))).thenReturn(true);
        return new DocumentWorkflow(repository, uploads, parser, chunker, redisson);
    }
    @Test void resumesPersistedTailEvenWhenCommittedBatchHadNoVisibleBlocks() throws Exception {
        var workflow = service("RETRYING", 1);
        var adaptive = mock(com.example.server.document.adaptive.AdaptiveDocumentRepository.class);
        workflow.adaptiveIngestion(adaptive);
        when(repository.get(ID)).thenReturn(new DocumentRecord(ID, 7L, "book.pdf", 10, 1, "source",
                "RETRYING", "PARSING", 0, 0, 1, null, null, "[]", LocalDateTime.now(), 2, 1, false, 1));
        when(repository.blocks(ID)).thenReturn(List.of());
        String state = "{\"version\":1,\"pending\":{\"text\":\"first half\",\"pages\":[1]}}";
        when(adaptive.resumeState(ID, 0)).thenReturn(state);
        when(uploads.materialize(any())).thenReturn(java.nio.file.Files.writeString(directory.resolve("resumed.pdf"), "test"));
        var batch = mapper.readTree("{\"type\":\"batch\",\"startPage\":2,\"endPage\":2,\"totalPages\":2,\"blocks\":[{\"text\":\"first half second half\",\"pages\":[1,2]}],\"warnings\":[]}");
        var units = List.of(new DocumentRepository.Block(0, "Body", "[]", "first half second half", null));
        when(adaptive.sourceUnits(batch, 0, "b1:")).thenReturn(units);
        doAnswer(call -> {
            var consumer = call.getArgument(7, DoclingClient.EventConsumer.class);
            consumer.accept(batch);
            consumer.accept(mapper.readTree("{\"type\":\"complete\",\"totalPages\":2}"));
            return null;
        }).when(parser).stream(any(), eq("book.pdf"), eq(1), eq(1), eq(""), eq("FAST"), eq(state), any());
        workflow.process(ID);
        verify(adaptive).resumeState(ID, 0);
        verify(adaptive).commitBatch(eq(ID), eq(1), eq(units), eq(2), eq(2), eq("[]"), eq(batch));
        verify(repository).parsed(ID);
        verify(adaptive).finish(ID);
        verify(parser, never()).stream(any(), anyString(), anyInt(), anyInt(), anyString(), anyString(), any());
        verify(repository, never()).failed(anyString(), anyString(), anyBoolean());
    }
    @Test void retrySkipsSavedBlocksAndDoesNotReparseOriginal() throws Exception {
        var done = new DocumentRepository.Block(0, "一", "[]", "原文", "{}");
        var pending = new DocumentRepository.Block(1, "二", "[]", "尾部", null);
        when(repository.blocks(ID)).thenReturn(List.of(done, pending));
        service("RETRYING", 1).process(ID);
        verifyNoInteractions(uploads, parser);
        verify(repository).begin(ID);
        verify(repository).parsed(ID);
        verify(repository, never()).complete(eq(ID), any());
    }
    @Test void completedRedeliveryCannotSpendMoreTokens() {
        service("COMPLETED", 1).process(ID);
        verifyNoInteractions(uploads, parser);
        verify(repository, never()).begin(anyString());
    }
    @Test void doclingOverrideIsScopedToTheSelectedDocument(){
        var workflow=service("COMPLETED",1);var doc=repository.get(ID);
        assertEquals("FAST",workflow.parserMode(doc));
        org.springframework.test.util.ReflectionTestUtils.setField(workflow,"doclingDocumentIds"," another-id, "+ID+" ");
        assertEquals("DOCLING",workflow.parserMode(doc));
        org.springframework.test.util.ReflectionTestUtils.setField(workflow,"doclingDocumentIds","another-id");
        assertEquals("FAST",workflow.parserMode(doc));
    }
    @Test void failedBlockIsNotReportedAsCompleteAndCanRetry() throws Exception {
        var pending = new DocumentRepository.Block(1, "二", "[]", "尾部", null);
        when(repository.blocks(ID)).thenReturn(List.of(pending));
        var workflow = service("PROCESSING", 1);
        workflow.process(ID);
        verify(repository, never()).failed(anyString(), anyString(), anyBoolean());
        verify(repository, never()).complete(anyString(), any());
        verify(repository, never()).knowledge(anyString(), anyInt(), any());
    }
    @Test void workerWithoutLockCannotCommitAnything() {
        var workflow = service("PROCESSING", 1);
        when(lock.tryLock()).thenReturn(false);
        workflow.process(ID);
        verifyNoInteractions(repository);
    }
    @Test void commitsEachBatchBeforeStreamCompletesAndRetainsCheckpointOnFailure() throws Exception {
        var workflow = service("PROCESSING", 1);
        when(repository.get(ID)).thenReturn(new DocumentRecord(ID, 7L, "book.pdf", 10, 1, "source",
                "PROCESSING", "PARSING", 0, 0, 1, null, null, "[]", LocalDateTime.now(), 2, 0, false, 0));
        when(uploads.materialize(any())).thenReturn(java.nio.file.Files.writeString(directory.resolve("book.pdf"), "test"));
        when(chunker.split(any(), eq(0), eq("b0:"))).thenReturn(List.of(new DocumentRepository.Block(0, "A", "[]", "source", null)));
        doAnswer(call -> {
            var consumer = call.getArgument(6, DoclingClient.EventConsumer.class);
            consumer.accept(mapper.readTree("{\"type\":\"batch\",\"startPage\":1,\"endPage\":1,\"totalPages\":2,\"blocks\":[{}],\"warnings\":[]}"));
            verify(repository).appendBatch(eq(ID), eq(0), anyList(), eq(1), eq(2), anyString());
                throw new IllegalStateException("second batch failed");
        }).when(parser).stream(any(), anyString(), eq(0), eq(0), eq(""), eq("FAST"), any());
        assertThrows(IllegalStateException.class, () -> workflow.process(ID));
        verify(repository, never()).parsed(anyString());
        verify(repository).failed(eq(ID), anyString(), eq(true));
        verify(repository, never()).saveBlocks(anyString(), anyList(), anyString());
    }
}
