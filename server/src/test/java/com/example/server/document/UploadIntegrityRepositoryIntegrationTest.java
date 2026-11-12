package com.example.server.document;

import com.example.server.knowledge.KnowledgeBaseService;
import com.example.server.utils.MinioUtils;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.redisson.api.RedissonClient;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicLong;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.mock;

/** Uses a fresh disposable MySQL schema, never the production media_db schema. */
@EnabledIfEnvironmentVariable(named="UPLOAD_TEST_DB_URL",matches=".+")
class UploadIntegrityRepositoryIntegrationTest {
    private static JdbcTemplate jdbc;
    private static DocumentRepository documents;
    private static UploadIntegrityRepository integrity;
    private static KnowledgeBaseService bases;
    private static ContentUploadService service;
    private static final AtomicLong USERS=new AtomicLong(10000);
    @BeforeAll static void schema(){
        String url=System.getenv("UPLOAD_TEST_DB_URL");
        assertTrue(url.matches("jdbc:mysql://[^/]+/upload_integrity_test_[a-f0-9]+(?:\\?.*)?"),"Only disposable upload test schemas allowed");
        var ds=new DriverManagerDataSource(url,System.getenv("UPLOAD_TEST_DB_USER"),System.getenv("UPLOAD_TEST_DB_PASSWORD"));
        new ResourceDatabasePopulator(new ClassPathResource("db/migration/V4__document_workspace.sql"),
                new ClassPathResource("db/migration/V6__document_pipeline.sql"),
                new ClassPathResource("db/migration/V11__personal_knowledge_bases.sql"),
                new ClassPathResource("db/migration/V14__verified_document_uploads.sql")).execute(ds);
        jdbc=new JdbcTemplate(ds);documents=new DocumentRepository(jdbc);integrity=new UploadIntegrityRepository(jdbc);
        var uploads=new DocumentUploadService(documents,mock(MinioUtils.class),mock(RedissonClient.class),209715200,integrity);
        bases=new KnowledgeBaseService(jdbc,(identity,resource,operation)->identity.userId().equals(resource.ownerId()),documents,uploads);
        service=new ContentUploadService(bases,documents,integrity,new DataSourceTransactionManager(ds),86400);
    }
    private static String hash(){
        try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(UUID.randomUUID().toString().getBytes()));}
        catch(Exception e){throw new IllegalStateException(e);}
    }
    private static ContentUploadService.Request request(String filename,String hash,String base){return new ContentUploadService.Request(filename,10,hash,base,null);}
    @Test void concurrentInitializationsCreateExactlyOneDocument()throws Exception{
        long user=USERS.incrementAndGet();String hash=hash();
        bases.list(user); // Existing users do not serialize behind initial default-library creation.
        try(var pool=Executors.newFixedThreadPool(4)){
            List<Callable<ContentUploadService.Session>> calls=java.util.stream.IntStream.range(0,12)
                    .mapToObj(i->(Callable<ContentUploadService.Session>)()->service.initialize(request("name"+i+".pdf",hash,null),user)).toList();
            var ids=new java.util.HashSet<String>();for(var future:pool.invokeAll(calls))ids.add(future.get().uploadId());
            assertEquals(1,ids.size());
            assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM document_files WHERE user_id=?",Integer.class,user));
        }
    }
    @Test void receiptsSurviveRenameAndCanOnlyBeInsertedOnce(){
        long user=USERS.incrementAndGet();String hash=hash();var first=service.initialize(request("a.pdf",hash,null),user);
        integrity.part(first.uploadId(),0,10,hash);
        assertThrows(org.springframework.dao.DuplicateKeyException.class,()->integrity.part(first.uploadId(),0,10,hash));
        var resumed=service.initialize(request("renamed.pdf",hash,null),user);
        assertEquals("RESUME",resumed.mode());assertEquals(first.uploadId(),resumed.uploadId());assertEquals(List.of(0),resumed.completedChunks());
        assertEquals(hash,integrity.parts(first.uploadId()).getFirst().sha256());
    }
    @Test void trustedInstantReuseDoesNotAllocateAnotherRevision(){
        long user=USERS.incrementAndGet();String hash=hash();var first=service.initialize(request("a.pdf",hash,null),user);
        documents.source(first.uploadId(),"http://minio/verified.pdf",hash);integrity.verified(first.uploadId());
        jdbc.update("UPDATE document_files SET status='COMPLETED' WHERE id=?",first.uploadId());
        var second=service.initialize(request("renamed.pdf",hash,null),user);
        assertEquals("INSTANT",second.mode());assertEquals(first.uploadId(),second.document().id());
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM document_files WHERE user_id=?",Integer.class,user));
    }
    @Test void contentMatchingRespectsOwnerAndKnowledgeBase(){
        long user=USERS.incrementAndGet(),other=USERS.incrementAndGet();String hash=hash();
        var a=service.initialize(request("a.pdf",hash,null),user);
        var b=service.initialize(request("a.pdf",hash,null),other);
        var base=bases.create(user,"second","personal");
        var c=service.initialize(request("a.pdf",hash,base.id()),user);
        assertEquals(3,java.util.Set.of(a.uploadId(),b.uploadId(),c.uploadId()).size());
        assertThrows(SecurityException.class,()->documents.owned(a.uploadId(),other));
    }
    @Test void renewalWinsAgainstStaleCleanupAndExpiredTasksCannotRenew(){
        long user=USERS.incrementAndGet();String hash=hash();var first=service.initialize(request("a.pdf",hash,null),user);String id=first.uploadId();
        jdbc.update("UPDATE document_upload_integrity SET expires_at=DATE_SUB(CURRENT_TIMESTAMP(3),INTERVAL 1 SECOND) WHERE document_id=?",id);
        assertTrue(integrity.touch(id,86400));assertFalse(integrity.expire(id));
        assertEquals("UPLOADING",documents.get(id).status());
        jdbc.update("UPDATE document_upload_integrity SET expires_at=DATE_SUB(CURRENT_TIMESTAMP(3),INTERVAL 1 SECOND) WHERE document_id=?",id);
        assertTrue(integrity.expire(id));assertFalse(integrity.touch(id,86400));assertEquals("CANCELLED",documents.get(id).status());
        assertNotEquals(id,service.initialize(request("a.pdf",hash,null),user).uploadId());
    }
    @Test void exhaustedMergeFailureIsEventuallySelectedForCleanup(){
        long user=USERS.incrementAndGet();var task=service.initialize(request("a.pdf",hash(),null),user);String id=task.uploadId();
        jdbc.update("UPDATE document_files SET status='FAILED' WHERE id=?",id);
        jdbc.update("UPDATE document_upload_integrity SET expires_at=DATE_SUB(CURRENT_TIMESTAMP(3),INTERVAL 1 SECOND) WHERE document_id=?",id);
        assertTrue(integrity.cleanupCandidates().contains(id));assertTrue(integrity.expire(id));
        integrity.cleaned(id);assertFalse(integrity.cleanupCandidates().contains(id));
    }
}
