package com.example.server.document;

import com.example.server.knowledge.KnowledgeBaseService;
import com.example.server.knowledge.PermissionChecker;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;

@Service
public class ContentUploadService {
    private final KnowledgeBaseService bases;
    private final DocumentRepository documents;
    private final UploadIntegrityRepository integrity;
    private final TransactionTemplate transactions;
    private final long ttl;
    public ContentUploadService(KnowledgeBaseService bases, DocumentRepository documents, UploadIntegrityRepository integrity,
                                PlatformTransactionManager manager, @Value("${document.upload-ttl-seconds:86400}") long ttl) {
        this.bases=bases; this.documents=documents; this.integrity=integrity;
        this.transactions=new TransactionTemplate(manager); this.ttl=Math.max(60,ttl);
        // A waiter reads the slot created by another transaction after acquiring its lock.
        // READ COMMITTED makes the referenced document visible even if authorization queries
        // ran before that transaction committed (MySQL REPEATABLE READ would retain that snapshot).
        this.transactions.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
    }
    public record Request(String filename, long sizeBytes, String sha256, String knowledgeBaseId, String previousDocumentId) {}
    public record Session(String uploadId, String mode, int chunkSizeBytes, int totalChunks,
                          List<Integer> completedChunks, DocumentRecord.View document) {}
    public Session initialize(Request request, Long user) {
        if(request==null || user==null)throw new IllegalArgumentException("缺少上传参数或用户");
        String filename=DocumentUploadService.normalizeFilename(request.filename());
        String hash=sha256(request.sha256());
        String base=request.knowledgeBaseId()==null||request.knowledgeBaseId().isBlank()?"personal-"+user:request.knowledgeBaseId();
        String previous=request.previousDocumentId()==null?"":request.previousDocumentId().strip();
        return transactions.execute(status->{
            // Initialization validates WRITE even when the request could be satisfied by deduplication.
            bases.list(user); bases.owned(base,user,PermissionChecker.Operation.WRITE);
            if(!previous.isEmpty())documents.writable(previous,user);
            String scope=scope(user,base,previous,filename.substring(filename.lastIndexOf('.')).toLowerCase(Locale.ROOT),request.sizeBytes(),hash);
            String existing=integrity.lockSlot(scope);
            if(existing!=null){
                DocumentRecord doc=documents.writable(existing,user);
                var metadata=integrity.get(existing);
                if(metadata!=null && "VERIFIED".equals(metadata.state()) && integrity.sourceMatches(existing,hash)
                        && List.of("COMPLETED","QUEUED","PROCESSING","RETRYING").contains(doc.status()))
                    return session(doc,"INSTANT",List.of(),doc.view());
                if(metadata!=null && "PENDING".equals(metadata.state()) &&
                        (("UPLOADING".equals(doc.status())&&!metadata.expired()) || List.of("QUEUED","PROCESSING","RETRYING").contains(doc.status()))){
                    if(integrity.touch(existing,ttl))return session(doc,"RESUME",documents.parts(existing),null);
                }
            }
            String id=bases.initialize(user,base,previous,filename,request.sizeBytes());
            integrity.create(id,hash,ttl); integrity.slot(scope,id);
            return session(documents.get(id),"NEW",List.of(),null);
        });
    }
    private Session session(DocumentRecord doc,String mode,List<Integer> completed,DocumentRecord.View view){
        return new Session(doc.id(),mode,DocumentUploadService.PART_BYTES,doc.totalParts(),completed,view);
    }
    public static String sha256(String value){
        if(value==null||!value.matches("(?i)[0-9a-f]{64}"))throw new IllegalArgumentException("SHA-256 必须为64位十六进制字符串");
        return value.toLowerCase(Locale.ROOT);
    }
    static String scope(Long user,String base,String previous,String extension,long bytes,String hash){
        try {
            // Length-delimited fields avoid collisions caused by delimiter characters in identifiers.
            var text=new StringBuilder();
            for(String field:List.of(user.toString(),base,previous,extension,Long.toString(bytes),hash))text.append(field.length()).append(':').append(field);
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(text.toString().getBytes(StandardCharsets.UTF_8)));
        }catch(java.security.NoSuchAlgorithmException e){throw new IllegalStateException(e);}
    }
}
