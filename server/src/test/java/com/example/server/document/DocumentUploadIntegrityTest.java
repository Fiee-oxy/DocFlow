package com.example.server.document;

import com.example.server.exception.BusinessException;
import com.example.server.utils.MinioUtils;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.mock.web.MockMultipartFile;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class DocumentUploadIntegrityTest {
    private static final String ID="91443c93-7f4b-43f8-91c0-22601ac83341";
    private static final byte[] PDF="%PDF-1.7\nx".getBytes(java.nio.charset.StandardCharsets.UTF_8);
    private final DocumentRepository documents=mock(DocumentRepository.class);
    private final UploadIntegrityRepository integrity=mock(UploadIntegrityRepository.class);
    private final MinioUtils minio=mock(MinioUtils.class);
    private final RedissonClient redisson=mock(RedissonClient.class);
    private DocumentUploadService service;
    private static String hash(byte[] bytes)throws Exception{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));}
    private DocumentRecord doc(String status,String source){
        return new DocumentRecord(ID,7L,"book.pdf",PDF.length,1,source,status,"UPLOADING",0,0,0,null,null,null,LocalDateTime.now());
    }
    @BeforeEach void setup()throws Exception{
        var lock=mock(RLock.class);when(lock.tryLock()).thenReturn(true);when(lock.isHeldByCurrentThread()).thenReturn(true);
        when(redisson.getLock(anyString())).thenReturn(lock);
        when(documents.writable(ID,7L)).thenReturn(doc("UPLOADING",null));
        when(documents.get(ID)).thenReturn(doc("UPLOADING",null));
        when(integrity.get(ID)).thenReturn(new UploadIntegrityRepository.Integrity(hash(PDF),"PENDING",LocalDateTime.now().plusDays(1)));
        when(integrity.parts(ID)).thenReturn(List.of());
        service=new DocumentUploadService(documents,minio,redisson,209715200,integrity);
    }
    private void upload(String declared)throws Exception{service.upload(ID,0,new MockMultipartFile("file",PDF),7L,declared);}
    private void storedBytes() {
        doAnswer(call->{((OutputStream)call.getArgument(1)).write(PDF);return null;}).when(minio).copyObjectTo(anyString(),any(OutputStream.class));
    }
    @Test void corruptedPartCannotWriteObjectOrReceipt()throws Exception{
        assertThrows(IllegalArgumentException.class,()->upload("0".repeat(64)));
        verifyNoInteractions(minio);verify(integrity,never()).part(anyString(),anyInt(),anyLong(),anyString());
    }
    @Test void missingPartHashCannotBypassNewProtocol(){
        assertThrows(IllegalArgumentException.class,()->upload(null));verifyNoInteractions(minio);
    }
    @Test void storageSuccessPrecedesReceiptAndAck()throws Exception{
        upload(hash(PDF));
        var order=inOrder(minio,integrity);
        order.verify(minio).uploadObject(eq("document-uploads/"+ID+"/part-0"),any(InputStream.class),eq((long)PDF.length),anyString());
        order.verify(integrity).part(ID,0,PDF.length,hash(PDF));
    }
    @Test void minioFailureNeverCreatesReceipt()throws Exception{
        when(minio.uploadObject(anyString(),any(InputStream.class),anyLong(),anyString())).thenThrow(new IllegalStateException("offline"));
        assertThrows(IllegalStateException.class,()->upload(hash(PDF)));
        verify(integrity,never()).part(anyString(),anyInt(),anyLong(),anyString());
    }
    @Test void databaseFailureAfterObjectCanRetrySafely()throws Exception{
        doThrow(new IllegalStateException("db offline")).doNothing().when(integrity).part(ID,0,PDF.length,hash(PDF));
        assertThrows(IllegalStateException.class,()->upload(hash(PDF)));
        upload(hash(PDF));
        verify(minio,times(2)).uploadObject(eq("document-uploads/"+ID+"/part-0"),any(InputStream.class),eq((long)PDF.length),anyString());
    }
    @Test void lostAckOrDuplicatePartDoesNotRewriteObject()throws Exception{
        when(integrity.parts(ID)).thenReturn(List.of(new UploadIntegrityRepository.Part(0,PDF.length,hash(PDF))));
        upload(hash(PDF));verifyNoInteractions(minio);
        verify(integrity,never()).part(anyString(),anyInt(),anyLong(),anyString());
    }
    @Test void sameIndexDifferentHashIsConflict()throws Exception{
        when(integrity.parts(ID)).thenReturn(List.of(new UploadIntegrityRepository.Part(0,PDF.length,hash(PDF))));
        assertThrows(BusinessException.class,()->upload("0".repeat(64)));verifyNoInteractions(minio);
    }
    @Test void completeOnlyQueuesVerifiedPartRecords()throws Exception{
        when(documents.parts(ID)).thenReturn(List.of(0));
        when(integrity.parts(ID)).thenReturn(List.of(new UploadIntegrityRepository.Part(0,PDF.length,hash(PDF))));
        when(documents.get(ID)).thenReturn(doc("QUEUED",null));
        assertEquals("QUEUED",service.complete(ID,7L).status());
        verify(documents).queue(ID,"UPLOADING");verifyNoInteractions(minio);
    }
    @Test void wholeFileMismatchBlocksPublicationAndParsing()throws Exception{
        storedBytes();when(documents.get(ID)).thenReturn(doc("QUEUED",null));
        when(integrity.get(ID)).thenReturn(new UploadIntegrityRepository.Integrity("0".repeat(64),"PENDING",LocalDateTime.now().plusDays(1)));
        var parser=mock(DoclingClient.class);
        new DocumentWorkflow(documents,service,parser,mock(DocumentChunker.class),redisson).process(ID);
        verify(documents).stage(ID,"VERIFY_FAILED");verify(integrity).failed(ID);
        verify(documents).failed(eq(ID),contains("SHA-256"),eq(false));
        verify(documents,never()).source(anyString(),anyString(),anyString());
        verify(minio,never()).uploadObject(anyString(),any(InputStream.class),anyLong(),anyString());verifyNoInteractions(parser);
    }
    @Test void validMergeVerifiesBeforeCleanup()throws Exception{
        storedBytes();when(minio.uploadObject(anyString(),any(InputStream.class),anyLong(),anyString())).thenReturn("http://minio/documents/book.pdf");
        var file=service.materialize(doc("QUEUED",null));
        try{assertArrayEquals(PDF,Files.readAllBytes(file));}finally{Files.deleteIfExists(file);}
        var order=inOrder(documents,integrity,minio);
        order.verify(documents).source(ID,"http://minio/documents/book.pdf",hash(PDF));
        order.verify(integrity).verified(ID);
        order.verify(minio).removeObject("document-uploads/"+ID+"/part-0");
    }
    @Test void interruptedVerificationAfterSourceCommitRechecksStoredSource()throws Exception{
        doAnswer(call->{((OutputStream)call.getArgument(1)).write(PDF);return null;}).when(minio).copyManagedFileTo(anyString(),any(OutputStream.class));
        var file=service.materialize(doc("RETRYING","http://minio/source.pdf"));Files.deleteIfExists(file);
        verify(integrity).verified(ID);verify(minio,never()).uploadLocalFile(any(),anyString(),anyString());
    }
    @Test void expiryRejectsResumeAndWrites()throws Exception{
        when(integrity.get(ID)).thenReturn(new UploadIntegrityRepository.Integrity(hash(PDF),"PENDING",LocalDateTime.now().minusSeconds(1)));
        assertThrows(BusinessException.class,()->service.parts(ID,7L));
        assertThrows(BusinessException.class,()->upload(hash(PDF)));verifyNoInteractions(minio);
    }
    @Test void cleanupDoesNotDeleteActiveUnverifiedTask()throws Exception{
        when(integrity.cleanupCandidates()).thenReturn(List.of(ID));when(documents.get(ID)).thenReturn(doc("PROCESSING",null));
        service.cleanupExpiredUploads();verifyNoInteractions(minio);
    }
    @Test void repeatCompleteDuringProcessingDoesNotWaitForWorkerLock(){
        when(documents.writable(ID,7L)).thenReturn(doc("PROCESSING",null));
        when(documents.get(ID)).thenReturn(doc("PROCESSING",null));
        assertEquals("PROCESSING",service.complete(ID,7L).status());
        verify(redisson,never()).getLock(anyString());verifyNoInteractions(minio);
    }
    @Test void sourceRecordFailureRetriesSameFinalObjectInsteadOfLeakingNewKeys()throws Exception{
        storedBytes();when(minio.uploadObject(anyString(),any(InputStream.class),anyLong(),anyString())).thenReturn("http://minio/fixed.pdf");
        doThrow(new IllegalStateException("db unavailable")).doNothing().when(documents).source(ID,"http://minio/fixed.pdf",hash(PDF));
        assertThrows(IllegalStateException.class,()->service.materialize(doc("QUEUED",null)));
        var file=service.materialize(doc("RETRYING",null));Files.deleteIfExists(file);
        verify(minio,times(2)).uploadObject(eq("documents/7/"+ID+"/source.pdf"),any(InputStream.class),eq((long)PDF.length),eq("application/pdf"));
    }
    @Test void fakePdfWithMatchingHashIsTerminalAndEligibleForCleanup()throws Exception{
        byte[] fake="not-a-pdf!".getBytes();
        when(integrity.get(ID)).thenReturn(new UploadIntegrityRepository.Integrity(hash(fake),"PENDING",LocalDateTime.now().plusDays(1)));
        doAnswer(call->{((OutputStream)call.getArgument(1)).write(fake);return null;}).when(minio).copyObjectTo(anyString(),any(OutputStream.class));
        assertThrows(IllegalArgumentException.class,()->service.materialize(doc("QUEUED",null)));
        verify(integrity).failed(ID);verify(documents).stage(ID,"VERIFY_FAILED");
        verify(minio,never()).uploadObject(anyString(),any(InputStream.class),anyLong(),anyString());
    }
    @Test void staleCleanupCannotDeleteTaskRenewedByResume()throws Exception{
        when(integrity.cleanupCandidates()).thenReturn(List.of(ID));
        when(integrity.get(ID)).thenReturn(new UploadIntegrityRepository.Integrity(hash(PDF),"PENDING",LocalDateTime.now().minusSeconds(1)));
        when(integrity.expire(ID)).thenReturn(false);
        service.cleanupExpiredUploads();verifyNoInteractions(minio);verify(integrity,never()).cleaned(ID);
    }
}
