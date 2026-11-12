package com.example.server.document;

import com.example.server.knowledge.KnowledgeBaseService;
import com.example.server.knowledge.PermissionChecker;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.mockito.ArgumentCaptor;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.SimpleTransactionStatus;

import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class ContentUploadServiceTest {
    private static final long USER = 7L;
    private static final long BYTES = 10L;
    private static final long TTL = 86400L;
    private static final String BASE = "personal-7";
    private static final String NEW_ID = "91443c93-7f4b-43f8-91c0-22601ac83341";
    private static final String EXISTING = "8b7cefd6-f217-4114-821c-f7819c041d59";
    private static final String HASH = "abcdef0123456789".repeat(4);
    private final KnowledgeBaseService bases = mock(KnowledgeBaseService.class);
    private final DocumentRepository documents = mock(DocumentRepository.class);
    private final UploadIntegrityRepository integrity = mock(UploadIntegrityRepository.class);
    private final PlatformTransactionManager manager = mock(PlatformTransactionManager.class);
    private final ContentUploadService service;

    ContentUploadServiceTest() {
        when(manager.getTransaction(any())).thenAnswer(call -> new SimpleTransactionStatus());
        when(integrity.touch(anyString(), anyLong())).thenReturn(true);
        when(bases.initialize(anyLong(), anyString(), anyString(), anyString(), anyLong())).thenReturn(NEW_ID);
        when(documents.get(NEW_ID)).thenReturn(doc(NEW_ID, "UPLOADING"));
        service = new ContentUploadService(bases, documents, integrity, manager, TTL);
    }

    @Test void newUploadPersistsNormalizedHashAndUsesDefaultKnowledgeBaseWithinTransaction() {
        var result = service.initialize(new ContentUploadService.Request("C:\\files\\Book.PDF", BYTES,
                HASH.toUpperCase(java.util.Locale.ROOT), null, null), USER);

        assertEquals("NEW", result.mode());
        assertEquals(NEW_ID, result.uploadId());
        assertEquals(DocumentUploadService.PART_BYTES, result.chunkSizeBytes());
        assertEquals(1, result.totalChunks());
        assertEquals(List.of(), result.completedChunks());
        assertNull(result.document());
        verify(bases).owned(BASE, USER, PermissionChecker.Operation.WRITE);
        verify(bases).initialize(USER, BASE, "", "Book.PDF", BYTES);
        verify(integrity).create(NEW_ID, HASH, TTL);
        verify(integrity).slot(anyString(), eq(NEW_ID));
        verify(manager).commit(any());
        verify(manager, never()).rollback(any());
    }

    @Test void renamedFileResumesSameContentAndReturnsOnlyServerConfirmedParts() {
        AtomicReference<String> slot = new AtomicReference<>();
        when(integrity.lockSlot(anyString())).thenAnswer(call -> slot.get());
        doAnswer(call -> { slot.set(call.getArgument(1)); return null; }).when(integrity).slot(anyString(), anyString());
        when(documents.writable(NEW_ID, USER)).thenReturn(doc(NEW_ID, "UPLOADING"));
        when(integrity.get(NEW_ID)).thenReturn(metadata("PENDING", false));
        when(documents.parts(NEW_ID)).thenReturn(List.of(0));

        var original = service.initialize(request("book.pdf", BASE, null), USER);
        var renamed = service.initialize(request("renamed.PDF", BASE, null), USER);

        assertEquals("NEW", original.mode());
        assertEquals("RESUME", renamed.mode());
        assertEquals(original.uploadId(), renamed.uploadId());
        assertEquals(List.of(0), renamed.completedChunks());
        assertNull(renamed.document());
        var keys = ArgumentCaptor.forClass(String.class);
        verify(integrity, times(2)).lockSlot(keys.capture());
        assertEquals(keys.getAllValues().get(0), keys.getAllValues().get(1));
        verify(bases, times(1)).initialize(anyLong(), anyString(), anyString(), anyString(), anyLong());
        verify(integrity).touch(NEW_ID, TTL);
    }

    @Test void instantUploadRequiresVerifiedSourceAndReauthorizesExistingDocument() {
        existing("COMPLETED", "VERIFIED", false);
        when(integrity.sourceMatches(EXISTING, HASH)).thenReturn(true);

        var result = service.initialize(request("new-name.pdf", BASE, null), USER);

        assertEquals("INSTANT", result.mode());
        assertEquals(EXISTING, result.uploadId());
        assertNotNull(result.document());
        assertEquals(EXISTING, result.document().id());
        assertEquals(List.of(), result.completedChunks());
        verify(documents).writable(EXISTING, USER);
        verify(integrity).sourceMatches(EXISTING, HASH);
        verify(bases, never()).initialize(anyLong(), anyString(), anyString(), anyString(), anyLong());
        verify(documents, never()).parts(anyString());
    }

    @ParameterizedTest
    @CsvSource({
            "UPLOADING, PENDING, true, false",
            "FAILED, FAILED, false, false",
            "CANCELLED, EXPIRED, true, false",
            "COMPLETED, VERIFIED, false, false",
            "COMPLETED, PENDING, false, true",
            "FAILED, VERIFIED, false, true"
    })
    void expiredFailedUnverifiedOrUnmatchedSourcesStartNewUpload(String status, String state,
                                                               boolean expired, boolean sourceMatches) {
        existing(status, state, expired);
        when(integrity.sourceMatches(EXISTING, HASH)).thenReturn(sourceMatches);

        var result = service.initialize(request("book.pdf", BASE, null), USER);

        assertEquals("NEW", result.mode());
        assertEquals(NEW_ID, result.uploadId());
        verify(integrity).create(NEW_ID, HASH, TTL);
        verify(integrity).slot(anyString(), eq(NEW_ID));
        verify(documents, never()).parts(EXISTING);
    }

    @Test void uploadExpiryDoesNotReplaceAlreadyQueuedPendingTask() {
        existing("QUEUED", "PENDING", true);
        when(documents.parts(EXISTING)).thenReturn(List.of(0));

        var result = service.initialize(request("renamed.pdf", BASE, null), USER);

        assertEquals("RESUME", result.mode());
        assertEquals(EXISTING, result.uploadId());
        verify(integrity).touch(EXISTING, TTL);
        verify(bases, never()).initialize(anyLong(), anyString(), anyString(), anyString(), anyLong());
    }

    @Test void renewalLostToExpiryCreatesNewTaskInsteadOfReturningDeletedParts() {
        existing("UPLOADING", "PENDING", false);
        when(integrity.touch(EXISTING, TTL)).thenReturn(false);

        var result = service.initialize(request("renamed.pdf", BASE, null), USER);

        assertEquals("NEW", result.mode());
        assertEquals(NEW_ID, result.uploadId());
        verify(documents, never()).parts(EXISTING);
        verify(integrity).create(NEW_ID, HASH, TTL);
        verify(integrity).slot(anyString(), eq(NEW_ID));
    }

    @Test void rejectedKnowledgeBasePermissionPreventsLookingUpContent() {
        doThrow(new SecurityException("read only")).when(bases).owned(BASE, USER, PermissionChecker.Operation.WRITE);

        assertThrows(SecurityException.class, () -> service.initialize(request("book.pdf", BASE, null), USER));

        verifyNoInteractions(integrity);
        verify(documents, never()).writable(anyString(), anyLong());
        verify(manager).rollback(any());
    }

    @Test void existingContentStillRequiresDocumentWritePermission() {
        when(integrity.lockSlot(anyString())).thenReturn(EXISTING);
        when(documents.writable(EXISTING, USER)).thenThrow(new SecurityException("denied"));

        assertThrows(SecurityException.class, () -> service.initialize(request("book.pdf", BASE, null), USER));

        verify(integrity, never()).get(anyString());
        verify(integrity, never()).sourceMatches(anyString(), anyString());
        verify(bases, never()).initialize(anyLong(), anyString(), anyString(), anyString(), anyLong());
    }

    @Test void previousVersionRequiresWritePermissionBeforeContentLookup() {
        when(documents.writable("previous", USER)).thenThrow(new SecurityException("denied"));

        assertThrows(SecurityException.class, () -> service.initialize(request("book.pdf", BASE, " previous "), USER));

        verify(documents).writable("previous", USER);
        verifyNoInteractions(integrity);
    }

    @Test void ownerKnowledgeBasePreviousVersionAndFormatHaveSeparateContentScopes() {
        service.initialize(request("book.pdf", BASE, null), USER);
        service.initialize(request("book.pdf", BASE, null), 8L);
        service.initialize(request("book.pdf", "another-base", null), USER);
        service.initialize(request("book.pdf", BASE, "previous"), USER);
        service.initialize(request("book.docx", BASE, null), USER);

        var keys = ArgumentCaptor.forClass(String.class);
        verify(integrity, times(5)).lockSlot(keys.capture());
        assertEquals(5, new HashSet<>(keys.getAllValues()).size());
        verify(documents).writable("previous", USER);
    }

    @Test void invalidHashIsRejectedBeforeTransactionOrContentLookup() {
        assertThrows(IllegalArgumentException.class, () -> service.initialize(
                new ContentUploadService.Request("book.pdf", BYTES, "not-a-sha256", BASE, null), USER));

        verifyNoInteractions(integrity, manager, documents, bases);
    }

    private void existing(String status, String state, boolean expired) {
        when(integrity.lockSlot(anyString())).thenReturn(EXISTING);
        when(documents.writable(EXISTING, USER)).thenReturn(doc(EXISTING, status));
        when(integrity.get(EXISTING)).thenReturn(metadata(state, expired));
    }

    private UploadIntegrityRepository.Integrity metadata(String state, boolean expired) {
        return new UploadIntegrityRepository.Integrity(HASH, state,
                expired ? LocalDateTime.now().minusHours(1) : LocalDateTime.now().plusHours(1));
    }

    private ContentUploadService.Request request(String filename, String base, String previous) {
        return new ContentUploadService.Request(filename, BYTES, HASH, base, previous);
    }

    private DocumentRecord doc(String id, String status) {
        return new DocumentRecord(id, USER, "book.pdf", BYTES, 1,
                "COMPLETED".equals(status) ? "source" : null, status, status,
                0, 0, 0, null, null, null, LocalDateTime.now());
    }
}
