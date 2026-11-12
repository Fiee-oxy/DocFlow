package com.example.server.document;

import com.example.server.utils.MinioUtils;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.mock.web.MockMultipartFile;
import java.nio.file.Path;
import java.nio.file.Files;
import java.time.LocalDateTime;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class DocumentUploadServiceTest {
    private static final String ID = "91443c93-7f4b-43f8-91c0-22601ac83341";
    private final DocumentRepository repository = mock(DocumentRepository.class);
    private final MinioUtils minio = mock(MinioUtils.class);
    private final RedissonClient redisson = mock(RedissonClient.class);
    private DocumentRecord doc(String status) {
        return new DocumentRecord(ID, 7L, "book.pdf", 10, 1, null, status, "UPLOADING", 0, 0, 0, null, null, null, LocalDateTime.now());
    }
    private DocumentUploadService service() {
        RLock lock = mock(RLock.class);
        when(redisson.getLock(anyString())).thenReturn(lock);
        when(lock.tryLock()).thenReturn(true);
        when(lock.isHeldByCurrentThread()).thenReturn(true);
        return new DocumentUploadService(repository, minio, redisson, 200 * 1024 * 1024);
    }
    @Test void completeOnlyQueuesAndNeverMergesInsideHttp() {
        when(repository.writable(ID, 7L)).thenReturn(doc("UPLOADING"));
        when(repository.get(ID)).thenReturn(doc("QUEUED"));
        when(repository.parts(ID)).thenReturn(List.of(0));
        assertEquals("QUEUED", service().complete(ID, 7L).status());
        verify(repository).queue(ID, "UPLOADING");
        verifyNoInteractions(minio);
    }
    @Test void completedUploadRetriesDoNotQueueOrUploadAgain() throws Exception {
        when(repository.writable(ID, 7L)).thenReturn(doc("COMPLETED"));
        when(repository.get(ID)).thenReturn(doc("COMPLETED"));
        var service = service();
        service.complete(ID, 7L);
        service.upload(ID, 0, new MockMultipartFile("file", new byte[10]), 7L);
        verify(repository, never()).queue(anyString(), anyString());
        verifyNoInteractions(minio);
    }
    @Test void invalidPartSizeCannotCreateObject() {
        when(repository.writable(ID, 7L)).thenReturn(doc("UPLOADING"));
        var service = service();
        assertThrows(IllegalArgumentException.class, () -> service.upload(ID, 0, new MockMultipartFile("file", new byte[9]), 7L));
        verifyNoInteractions(minio);
    }
    @Test void rejectsVideoAndFakePdf(@TempDir Path directory) throws Exception {
        assertThrows(IllegalArgumentException.class, () -> DocumentUploadService.normalizeFilename("sample.mp4"));
        Path fake = Files.writeString(directory.resolve("fake.pdf"), "not-a-pdf");
        assertThrows(IllegalArgumentException.class, () -> DocumentUploadService.validateFile(fake, "fake.pdf"));
    }
}
