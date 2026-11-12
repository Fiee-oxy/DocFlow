package com.example.server.document;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import java.time.LocalDateTime;
import java.util.List;

/** Hash declarations are separate from the server-computed document_files.content_hash. */
@Repository
public class UploadIntegrityRepository {
    private final JdbcTemplate jdbc;
    public UploadIntegrityRepository(JdbcTemplate jdbc) { this.jdbc = jdbc; }
    public record Integrity(String expectedSha256, String state, LocalDateTime expiresAt) {
        public boolean expired() { return expiresAt.isBefore(LocalDateTime.now()); }
    }
    public record Part(int index, long size, String sha256) {}
    public Integrity get(String id) {
        return jdbc.query("SELECT expected_sha256,verification_state,expires_at FROM document_upload_integrity WHERE document_id=?",
                (rs,n)->new Integrity(rs.getString(1),rs.getString(2),rs.getTimestamp(3).toLocalDateTime()),id)
                .stream().findFirst().orElse(null);
    }
    public void create(String id, String hash, long ttlSeconds) {
        jdbc.update("INSERT INTO document_upload_integrity(document_id,expected_sha256,expires_at) VALUES(?,?,TIMESTAMPADD(SECOND,?,CURRENT_TIMESTAMP(3)))",id,hash,ttlSeconds);
    }
    public boolean touch(String id, long ttlSeconds) {
        int changed=jdbc.update("UPDATE document_upload_integrity SET expires_at=TIMESTAMPADD(SECOND,?,CURRENT_TIMESTAMP(3)) WHERE document_id=? AND verification_state='PENDING'",ttlSeconds,id);
        jdbc.update("UPDATE document_files SET updated_at=CURRENT_TIMESTAMP(3) WHERE id=? AND status='UPLOADING'",id);
        return changed>0;
    }
    public String lockSlot(String scope) {
        // Duplicate-key UPDATE takes an exclusive lock immediately; INSERT IGNORE followed
        // by FOR UPDATE can deadlock when concurrent readers upgrade shared record locks.
        jdbc.update("INSERT INTO document_upload_slots(scope_key) VALUES(?) ON DUPLICATE KEY UPDATE scope_key=VALUES(scope_key)",scope);
        return jdbc.queryForObject("SELECT document_id FROM document_upload_slots WHERE scope_key=? FOR UPDATE",String.class,scope);
    }
    public void slot(String scope, String id) { jdbc.update("UPDATE document_upload_slots SET document_id=? WHERE scope_key=?",id,scope); }
    public List<Part> parts(String id) {
        return jdbc.query("SELECT part_index,size_bytes,sha256 FROM document_parts WHERE document_id=? ORDER BY part_index",
                (rs,n)->new Part(rs.getInt(1),rs.getLong(2),rs.getString(3)),id);
    }
    public void part(String id, int index, long size, String hash) {
        jdbc.update("INSERT INTO document_parts(document_id,part_index,size_bytes,sha256) VALUES(?,?,?,?)",id,index,size,hash);
    }
    public void verified(String id) {
        jdbc.update("UPDATE document_upload_integrity SET verification_state='VERIFIED',verified_at=CURRENT_TIMESTAMP(3) WHERE document_id=?",id);
    }
    public void failed(String id) { jdbc.update("UPDATE document_upload_integrity SET verification_state='FAILED' WHERE document_id=?",id); }
    public boolean sourceMatches(String id, String hash) {
        return Boolean.TRUE.equals(jdbc.queryForObject("SELECT EXISTS(SELECT 1 FROM document_files WHERE id=? AND source_path IS NOT NULL AND content_hash=?)",Boolean.class,id,hash));
    }
    public List<String> cleanupCandidates() {
        return jdbc.queryForList("""
                SELECT i.document_id FROM document_upload_integrity i JOIN document_files f ON f.id=i.document_id
                WHERE i.cleanup_pending=TRUE AND (i.verification_state='VERIFIED' OR f.status='CANCELLED'
                  OR (i.expires_at<CURRENT_TIMESTAMP(3) AND (f.status IN ('UPLOADING','FAILED') OR i.verification_state IN ('FAILED','EXPIRED')))) LIMIT 100
                """,String.class);
    }
    public boolean expire(String id) {
        return jdbc.update("""
                UPDATE document_upload_integrity i JOIN document_files f ON f.id=i.document_id
                SET i.verification_state='EXPIRED',f.status='CANCELLED',f.stage='UPLOAD_EXPIRED',
                    f.error_message='上传任务已过期，请重新上传'
                WHERE i.document_id=? AND i.expires_at<CURRENT_TIMESTAMP(3)
                    AND f.status IN ('UPLOADING','FAILED') AND i.verification_state<>'VERIFIED'
                """,id)>0;
    }
    public void cleaned(String id) { jdbc.update("UPDATE document_upload_integrity SET cleanup_pending=FALSE WHERE document_id=?",id); }
}
