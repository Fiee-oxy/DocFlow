package com.example.server.document;

import com.example.server.common.ErrorCode;
import com.example.server.exception.BusinessException;
import com.example.server.utils.MinioUtils;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.InputStream;
import java.io.OutputStream;
import java.io.ByteArrayInputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.DigestOutputStream;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.zip.ZipFile;

@Service
public class DocumentUploadService {
    public static final int PART_BYTES = 5 * 1024 * 1024;
    private final DocumentRepository repository;
    private final MinioUtils minio;
    private final RedissonClient redisson;
    private final long maxBytes;
    private final UploadIntegrityRepository integrity;
    @Value("${document.upload-ttl-seconds:86400}") private long ttlSeconds = 86400;
    public DocumentUploadService(DocumentRepository repository, MinioUtils minio, RedissonClient redisson,
                                 @Value("${document.max-file-bytes:209715200}") long maxBytes) {
        this(repository,minio,redisson,maxBytes,null);
    }
    @org.springframework.beans.factory.annotation.Autowired
    public DocumentUploadService(DocumentRepository repository, MinioUtils minio, RedissonClient redisson,
                                 @Value("${document.max-file-bytes:209715200}") long maxBytes,
                                 UploadIntegrityRepository integrity) {
        this.repository = repository;
        this.minio = minio;
        this.redisson = redisson;
        this.maxBytes = maxBytes;
        this.integrity = integrity;
    }
    public String initialize(String filename, long size, Long userId) {
        String name = normalizeFilename(filename);
        if (size <= 0 || size > maxBytes) throw new IllegalArgumentException("文档大小必须在 1 字节至 " + (maxBytes / 1024 / 1024) + " MiB 之间");
        String id = UUID.randomUUID().toString();
        repository.create(id, userId, name, size, (int) ((size + PART_BYTES - 1) / PART_BYTES));
        return id;
    }
    public List<Integer> parts(String id, Long userId) {
        repository.owned(id, userId);
        requireUsable(id);
        return repository.parts(id);
    }
    public void upload(String id, int index, MultipartFile file, Long userId) throws Exception {
        upload(id,index,file,userId,null);
    }
    public void upload(String id, int index, MultipartFile file, Long userId, String sha256) throws Exception {
        RLock lock = redisson.getLock(lockKey(id));
        if (!lock.tryLock()) throw new BusinessException(ErrorCode.RATE_LIMITED, "文档正在接收分片，请稍后重试");
        try {
            DocumentRecord doc = repository.writable(id, userId);
            if (index < 0 || index >= doc.totalParts()) throw new IllegalArgumentException("分片序号不合法");
            long expected = Math.min(PART_BYTES, doc.sizeBytes() - (long) index * PART_BYTES);
            if (file == null || file.getSize() != expected) throw new IllegalArgumentException("分片大小与声明的文档大小不一致");
            var metadata = requireUsable(id);
            if (metadata != null) {
                String declared = ContentUploadService.sha256(sha256);
                var receipt = integrity.parts(id).stream().filter(p -> p.index()==index).findFirst().orElse(null);
                if (receipt != null) {
                    if (!declared.equals(receipt.sha256())) throw new BusinessException(ErrorCode.CONFLICT,"同一分片编号的内容指纹不一致，请重新选择原文件");
                    if("UPLOADING".equals(doc.status()))integrity.touch(id,ttlSeconds);
                    return; // A lost ACK must not rewrite an already confirmed object.
                }
                if(!"UPLOADING".equals(doc.status()))throw new BusinessException(ErrorCode.CONFLICT,"上传已冻结，不能写入新分片");
                byte[] bytes;
                try(InputStream input=file.getInputStream()){bytes=input.readNBytes((int)expected+1);}
                if(bytes.length!=expected)throw new IllegalArgumentException("分片实际大小不匹配");
                String actual=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
                if(!actual.equals(declared))throw new IllegalArgumentException("分片 SHA-256 校验失败，请重传该片");
                try(InputStream input=new ByteArrayInputStream(bytes)){
                    minio.uploadObject(partKey(id,index),input,expected,"application/octet-stream");
                }
                if(!lock.isHeldByCurrentThread())throw new IllegalStateException("分片执行锁已失效");
                integrity.part(id,index,expected,actual);
                integrity.touch(id,ttlSeconds);
                return;
            }
            // Lost complete responses may be followed by late part retries.
            if (!"UPLOADING".equals(doc.status())) return;
            try (InputStream input = file.getInputStream()) {
                minio.uploadObject(partKey(id, index), input, expected, "application/octet-stream");
            }
            repository.part(id, index, expected);
        } finally { if (lock.isHeldByCurrentThread()) lock.unlock(); }
    }
    public DocumentRecord.View complete(String id, Long userId) {
        // A processing worker holds the document lock; repeated completion needs only a read.
        DocumentRecord initial=repository.writable(id,userId);
        requireUsable(id);
        if(List.of("QUEUED","PROCESSING","RETRYING","COMPLETED").contains(initial.status()))return initial.view();
        if(!"UPLOADING".equals(initial.status()))throw new BusinessException(ErrorCode.CONFLICT,"当前上传任务不可提交，请重新上传或使用任务重试");
        RLock lock = redisson.getLock(lockKey(id));
        if (!lock.tryLock()) throw new BusinessException(ErrorCode.RATE_LIMITED, "文档正在接收分片，请稍后重试");
        try {
            DocumentRecord doc = repository.writable(id, userId);
            var metadata=requireUsable(id);
            if ("UPLOADING".equals(doc.status())) {
                if (repository.parts(id).size() != doc.totalParts()) throw new BusinessException(ErrorCode.CONFLICT, "分片尚未全部上传完成");
                if(metadata!=null){
                    var parts=integrity.parts(id);
                    if(parts.size()!=doc.totalParts())throw new BusinessException(ErrorCode.CONFLICT,"分片完成记录不完整");
                    for(int i=0;i<parts.size();i++){
                        var part=parts.get(i);
                        if(part.index()!=i || part.size()!=Math.min(PART_BYTES,doc.sizeBytes()-(long)i*PART_BYTES) || part.sha256()==null)
                            throw new BusinessException(ErrorCode.CONFLICT,"分片编号、大小或哈希记录不完整");
                    }
                }
                repository.queue(id, "UPLOADING");
            }
            return repository.get(id).view();
        } finally { if (lock.isHeldByCurrentThread()) lock.unlock(); }
    }

    /** Runs only inside the queue worker; no large merge in the HTTP request. */
    public Path materialize(DocumentRecord doc) throws Exception {
        Path file = Files.createTempFile("document-", doc.filename().toLowerCase(Locale.ROOT).endsWith(".pdf") ? ".pdf" : ".docx");
        try {
            var metadata=integrity==null?null:integrity.get(doc.id());
            if(metadata!=null && List.of("FAILED","EXPIRED").contains(metadata.state()))
                throw new IllegalArgumentException("上传完整性校验失败或任务已过期，请重新上传");
            MessageDigest digest=MessageDigest.getInstance("SHA-256");
            if (doc.sourcePath() != null) {
                try (OutputStream out = new DigestOutputStream(Files.newOutputStream(file),digest)) { minio.copyManagedFileTo(doc.sourcePath(), out); }
            } else {
                try (OutputStream out = new DigestOutputStream(Files.newOutputStream(file), digest)) {
                    for (int i = 0; i < doc.totalParts(); i++) minio.copyObjectTo(partKey(doc.id(), i), out);
                }
            }
            if(Files.size(file)!=doc.sizeBytes())throw integrityFailure(doc.id(),"合并后的文档大小不匹配");
            String actual=HexFormat.of().formatHex(digest.digest());
            if(metadata!=null && !actual.equals(metadata.expectedSha256()))
                throw integrityFailure(doc.id(),"整文件 SHA-256 校验失败，已停止解析，请重新选择原文件上传");
            try{validateFile(file,doc.filename());}
            catch(IllegalArgumentException invalid){throw integrityFailure(doc.id(),invalid.getMessage());}
            if(doc.sourcePath()==null){
                String source;
                if(metadata!=null){
                    try(InputStream input=Files.newInputStream(file)){
                        source=minio.uploadObject(finalKey(doc),input,doc.sizeBytes(),doc.filename().toLowerCase(Locale.ROOT).endsWith(".pdf")?"application/pdf":"application/vnd.openxmlformats-officedocument.wordprocessingml.document");
                    }
                }else source=minio.uploadLocalFile(file.toFile(),doc.filename(),"documents/"+doc.userId());
                repository.source(doc.id(), source, actual);
            }
            if(metadata!=null)integrity.verified(doc.id());
            if(cleanupParts(doc)&&metadata!=null)integrity.cleaned(doc.id());
            return file;
        } catch (Exception e) { Files.deleteIfExists(file); throw e; }
    }
    private boolean cleanupParts(DocumentRecord doc) {
        boolean success=true;
        for (int i = 0; i < doc.totalParts(); i++) {
            try { minio.removeObject(partKey(doc.id(), i)); }
            catch (RuntimeException ignored) { success=false; }
        }
        return success;
    }
    private UploadIntegrityRepository.Integrity requireUsable(String id){
        var metadata=integrity==null?null:integrity.get(id);
        if(metadata!=null && (List.of("FAILED","EXPIRED").contains(metadata.state())
                || ("UPLOADING".equals(repository.get(id).status())&&metadata.expired())))
            throw new BusinessException(ErrorCode.CONFLICT,"上传任务已过期或校验失败，请重新初始化上传");
        return metadata;
    }
    private IllegalArgumentException integrityFailure(String id,String message){
        if(integrity!=null){integrity.failed(id);repository.stage(id,"VERIFY_FAILED");}
        return new IllegalArgumentException(message);
    }
    public void requireRetryable(String id){requireUsable(id);}
    @org.springframework.scheduling.annotation.Scheduled(fixedDelayString="${document.upload-cleanup-interval-ms:600000}",initialDelay=60000)
    public void cleanupExpiredUploads(){
        if(integrity==null)return;
        for(String id:integrity.cleanupCandidates()){
            RLock lock=redisson.getLock(lockKey(id));
            if(!lock.tryLock())continue;
            try{
                var doc=repository.get(id); var metadata=integrity.get(id);
                if(metadata==null)continue;
                if("UPLOADING".equals(doc.status())&&metadata.expired()){
                    if(!integrity.expire(id))continue; // A simultaneous resume may already have renewed the task.
                }
                else if((List.of("FAILED","EXPIRED").contains(metadata.state())||"FAILED".equals(doc.status()))&&metadata.expired()) {
                    if(!"CANCELLED".equals(doc.status())&&!integrity.expire(id))continue;
                }
                else if(!"VERIFIED".equals(metadata.state())&&!"CANCELLED".equals(doc.status()))continue;
                if(!"VERIFIED".equals(metadata.state())&&doc.sourcePath()==null)minio.removeObject(finalKey(doc));
                if(cleanupParts(doc))integrity.cleaned(doc.id());
            }finally{if(lock.isHeldByCurrentThread())lock.unlock();}
        }
    }
    static String normalizeFilename(String filename) {
        if (filename == null || filename.isBlank()) throw new IllegalArgumentException("文档名称不能为空");
        String name = filename.replace('\\', '/');
        name = name.substring(name.lastIndexOf('/') + 1).trim();
        if (name.length() > 255 || name.chars().anyMatch(Character::isISOControl)
                || !name.toLowerCase(Locale.ROOT).matches(".+\\.(pdf|docx)")) {
            throw new IllegalArgumentException("仅支持 PDF 和 DOCX 文档");
        }
        return name;
    }
    static void validateFile(Path file, String filename) throws Exception {
        if (filename.toLowerCase(Locale.ROOT).endsWith(".pdf")) {
            try (InputStream input = Files.newInputStream(file)) {
                String header = new String(input.readNBytes(1024), java.nio.charset.StandardCharsets.ISO_8859_1);
                if (!header.contains("%PDF-")) throw new IllegalArgumentException("文件内容不是有效 PDF");
            }
        } else if (filename.toLowerCase(Locale.ROOT).endsWith(".docx")) {
            try (ZipFile zip = new ZipFile(file.toFile())) {
                if (zip.getEntry("word/document.xml") == null || zip.getEntry("[Content_Types].xml") == null)
                    throw new IllegalArgumentException("文件内容不是有效 DOCX");
                long expanded = 0;
                int entries = 0;
                var iterator = zip.entries();
                while (iterator.hasMoreElements()) {
                    var entry = iterator.nextElement();
                    if (++entries > 100_000 || entry.getSize() < 0 || entry.getSize() > 512L * 1024 * 1024)
                        throw new IllegalArgumentException("DOCX 解压结构超过处理限制");
                    expanded += entry.getSize();
                    if (expanded > 2L * 1024 * 1024 * 1024) throw new IllegalArgumentException("DOCX 解压大小超过处理限制");
                }
            } catch (java.util.zip.ZipException e) { throw new IllegalArgumentException("DOCX 文件已损坏", e); }
        }
    }
    static String lockKey(String id) {
        try { UUID.fromString(id); } catch (Exception e) { throw new IllegalArgumentException("无效的文档 ID"); }
        return "lock:document:" + id;
    }
    private String partKey(String id, int index) { return "document-uploads/" + id + "/part-" + index; }
    private String finalKey(DocumentRecord doc){return "documents/"+doc.userId()+"/"+doc.id()+"/source"+(doc.filename().toLowerCase(Locale.ROOT).endsWith(".pdf")?".pdf":".docx");}
}
