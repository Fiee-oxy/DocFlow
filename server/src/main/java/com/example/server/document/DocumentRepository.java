package com.example.server.document;

import com.fasterxml.jackson.databind.JsonNode;
import com.example.server.service.ModelFailure;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.NoSuchElementException;

@Repository
public class DocumentRepository {
    private final JdbcTemplate jdbc;
    private com.example.server.knowledge.PermissionChecker permissions=(identity,resource,operation)->identity.userId().equals(resource.ownerId());
    private com.example.server.knowledge.KnowledgeIdentityAdapter identities=com.example.server.knowledge.KnowledgeIdentity::new;
    @org.springframework.beans.factory.annotation.Autowired
    public void identityAdapter(com.example.server.knowledge.KnowledgeIdentityAdapter adapter){this.identities=adapter;}
    @org.springframework.beans.factory.annotation.Autowired
    public void permissionChecker(com.example.server.knowledge.PermissionChecker checker){this.permissions=checker;}
    private static final RowMapper<DocumentRecord> MAPPER = (rs, n) -> new DocumentRecord(
            rs.getString("id"), rs.getLong("user_id"), rs.getString("filename"), rs.getLong("size_bytes"),
            rs.getInt("total_parts"), rs.getString("source_path"), rs.getString("status"), rs.getString("stage"),
            rs.getInt("total_blocks"), rs.getInt("completed_blocks"), rs.getInt("attempts"),
            rs.getString("error_message"), rs.getString("report_json"), rs.getString("parse_warnings"), rs.getTimestamp("created_at").toLocalDateTime(),
            rs.getInt("total_pages"), rs.getInt("parsed_pages"), rs.getBoolean("parse_complete"), rs.getInt("parse_batches"));

    public DocumentRepository(JdbcTemplate jdbc) { this.jdbc = jdbc; }
    public void parserAudit(String id,JsonNode event){
        jdbc.update("INSERT INTO document_parser_audit(document_id,event_type,payload) VALUES(?,?,?)",id,event.path("type").asText(),event.toString());
    }
    public List<String> parserAudits(String id){return jdbc.queryForList("SELECT payload FROM document_parser_audit WHERE document_id=? ORDER BY id DESC LIMIT 100",String.class,id);}
    public String parserIR(String id,int batch){
        var rows=jdbc.queryForList("SELECT JSON_EXTRACT(payload,'$.documentIR') FROM document_parse_batches WHERE document_id=? AND batch_index=?",String.class,id,batch);
        if(rows.isEmpty()||rows.getFirst()==null)throw new IllegalArgumentException("该批次没有 Document IR；旧数据不会被自动重新解析");
        return rows.getFirst();
    }
    @org.springframework.scheduling.annotation.Scheduled(fixedDelay=86400000,initialDelay=60000)
    public void cleanParserAudits(){jdbc.update("DELETE FROM document_parser_audit WHERE created_at<DATE_SUB(CURRENT_TIMESTAMP,INTERVAL 7 DAY) LIMIT 1000");}
    public void cancelParsing(String id){
        jdbc.update("UPDATE document_files SET status='CANCELLED',stage='CANCELLED',updated_at=CURRENT_TIMESTAMP(3) WHERE id=? AND parse_complete=FALSE AND status IN ('UPLOADING','QUEUED','RETRYING','PROCESSING')",id);
    }

    public DocumentRecord get(String id) {
        return jdbc.query("SELECT * FROM document_files WHERE id=?", MAPPER, id).stream().findFirst()
                .orElseThrow(() -> new NoSuchElementException("文档不存在"));
    }
    public int revisionNumber(String id){
        return jdbc.queryForObject("SELECT version_number FROM knowledge_documents WHERE document_id=?",Integer.class,id);
    }
    public DocumentRecord owned(String id, Long userId) {
        DocumentRecord doc = get(id);
        if (!doc.userId().equals(userId)) throw new SecurityException("无权访问该文档");
        permissions.require(identities.resolveAuthenticatedUser(userId),
                new com.example.server.knowledge.PermissionChecker.Resource("DOCUMENT",id,doc.userId(),""),
                com.example.server.knowledge.PermissionChecker.Operation.READ);
        for(var base:jdbc.queryForList("SELECT b.id,b.source_module FROM knowledge_bases b JOIN knowledge_documents k ON k.knowledge_base_id=b.id WHERE k.document_id=?",id))
            permissions.require(identities.resolveAuthenticatedUser(userId),
                    new com.example.server.knowledge.PermissionChecker.Resource("KNOWLEDGE_BASE",base.get("id").toString(),doc.userId(),base.get("source_module").toString()),
                    com.example.server.knowledge.PermissionChecker.Operation.READ);
        return doc;
    }
    public DocumentRecord writable(String id,Long userId){
        var doc=owned(id,userId);
        permissions.require(identities.resolveAuthenticatedUser(userId),
                new com.example.server.knowledge.PermissionChecker.Resource("DOCUMENT",id,doc.userId(),""),
                com.example.server.knowledge.PermissionChecker.Operation.WRITE);
        for(var base:jdbc.queryForList("SELECT b.id,b.source_module FROM knowledge_bases b JOIN knowledge_documents k ON k.knowledge_base_id=b.id WHERE k.document_id=?",id))
            permissions.require(identities.resolveAuthenticatedUser(userId),
                    new com.example.server.knowledge.PermissionChecker.Resource("KNOWLEDGE_BASE",base.get("id").toString(),doc.userId(),base.get("source_module").toString()),
                    com.example.server.knowledge.PermissionChecker.Operation.WRITE);
        return doc;
    }
    /** Event streams reauthorize their whole scope in one metadata query, without loading reports. */
    public void authorizeScope(java.util.List<String> ids,Long userId){
        if(ids==null||ids.isEmpty()||ids.size()>64)throw new IllegalArgumentException("文档范围不合法");
        var distinct=ids.stream().distinct().toList();
        String marks=String.join(",",java.util.Collections.nCopies(distinct.size(),"?"));
        var rows=jdbc.queryForList("SELECT f.id,f.user_id,b.id AS base_id,b.owner_id AS base_owner,b.source_module FROM document_files f LEFT JOIN knowledge_documents k ON k.document_id=f.id LEFT JOIN knowledge_bases b ON b.id=k.knowledge_base_id WHERE f.id IN ("+marks+")",distinct.toArray());
        if(rows.size()!=distinct.size())throw new NoSuchElementException("文档范围已变化");
        var identity=identities.resolveAuthenticatedUser(userId);
        for(var row:rows){
            if(((Number)row.get("user_id")).longValue()!=userId || row.get("base_owner")==null || ((Number)row.get("base_owner")).longValue()!=userId)
                throw new SecurityException("无权访问该文档范围");
            permissions.require(identity,new com.example.server.knowledge.PermissionChecker.Resource("DOCUMENT",row.get("id").toString(),userId,""),com.example.server.knowledge.PermissionChecker.Operation.READ);
            permissions.require(identity,new com.example.server.knowledge.PermissionChecker.Resource("KNOWLEDGE_BASE",row.get("base_id").toString(),userId,row.get("source_module").toString()),com.example.server.knowledge.PermissionChecker.Operation.READ);
        }
    }
    public List<DocumentRecord.View> list(Long userId) {
        // Reports can be large: list queries deliberately omit their payload.
        return jdbc.query("""
                SELECT id,filename,size_bytes,status,stage,total_blocks,completed_blocks,error_message,created_at,total_pages,parsed_pages,parse_complete
                FROM document_files WHERE user_id=? ORDER BY created_at DESC LIMIT 100
                """, (rs, n) -> new DocumentRecord.View(rs.getString("id"), rs.getString("filename"),
                rs.getLong("size_bytes"), rs.getString("status"), rs.getString("stage"), rs.getInt("total_blocks"),
                rs.getInt("completed_blocks"), rs.getString("error_message"), rs.getTimestamp("created_at").toLocalDateTime(),
                rs.getInt("total_pages"), rs.getInt("parsed_pages"), rs.getBoolean("parse_complete")), userId).stream()
                .filter(doc->{try{owned(doc.id(),userId);return true;}catch(SecurityException denied){return false;}}).toList();
    }
    @Transactional
    public void create(String id, Long userId, String filename, long bytes, int parts) {
        permissions.require(identities.resolveAuthenticatedUser(userId),
                new com.example.server.knowledge.PermissionChecker.Resource("DOCUMENT",id,userId,""),
                com.example.server.knowledge.PermissionChecker.Operation.WRITE);
        jdbc.update("INSERT INTO document_files(id,user_id,filename,size_bytes,total_parts) VALUES(?,?,?,?,?)",
                id, userId, filename, bytes, parts);
        com.example.server.knowledge.KnowledgeBaseService.ensureDefault(jdbc,userId);
        jdbc.update("INSERT INTO knowledge_documents(document_id,knowledge_base_id,owner_id,logical_document_id,version_number) VALUES(?,?,?,?,1)",id,"personal-"+userId,userId,id);
    }
    public List<Integer> parts(String id) {
        return jdbc.queryForList("SELECT part_index FROM document_parts WHERE document_id=? ORDER BY part_index", Integer.class, id);
    }
    public void part(String id, int index, long bytes) {
        jdbc.update("""
                INSERT INTO document_parts(document_id,part_index,size_bytes) VALUES(?,?,?)
                ON DUPLICATE KEY UPDATE size_bytes=VALUES(size_bytes)
                """, id, index, bytes);
    }
    public boolean queue(String id, String from) {
        return jdbc.update("""
                UPDATE document_files SET status='QUEUED',error_message=NULL,attempts=0,updated_at=CURRENT_TIMESTAMP(3)
                WHERE id=? AND status=?
                """, id, from) == 1;
    }
    @Transactional
    public void resetFailedKnowledge(String id) {
        jdbc.update("UPDATE document_blocks SET knowledge_attempts=0 WHERE document_id=? AND knowledge_json IS NULL", id);
    }
    public void begin(String id) {
        jdbc.update("UPDATE document_files SET status='PROCESSING',attempts=attempts+1,error_message=NULL,updated_at=CURRENT_TIMESTAMP(3) WHERE id=? AND status<>'CANCELLED'", id);
    }
    public void stage(String id, String stage) {
        jdbc.update("UPDATE document_files SET stage=?,updated_at=CURRENT_TIMESTAMP(3) WHERE id=?", stage, id);
    }
    public void parserWarnings(String id,String warnings) {
        jdbc.update("UPDATE document_files SET parse_warnings=?,updated_at=CURRENT_TIMESTAMP(3) WHERE id=? AND status<>'CANCELLED'",warnings,id);
    }
    public void heartbeat(String id) {
        jdbc.update("UPDATE document_files SET updated_at=CURRENT_TIMESTAMP(3) WHERE id=? AND status='PROCESSING'", id);
    }
    public void source(String id, String path, String hash) {
        jdbc.update("UPDATE document_files SET source_path=?,content_hash=?,updated_at=CURRENT_TIMESTAMP(3) WHERE id=?", path, hash, id);
    }
    public List<Block> blocks(String id) {
        return jdbc.query("SELECT * FROM document_blocks WHERE document_id=? ORDER BY block_index", (rs, n) ->
                new Block(rs.getInt("block_index"), rs.getString("section_title"), rs.getString("source_json"),
                        rs.getString("text_content"), rs.getString("knowledge_json")), id);
    }
    public Block block(String id, int index) {
        return jdbc.query("SELECT * FROM document_blocks WHERE document_id=? AND block_index=?", (rs, n) ->
                new Block(rs.getInt("block_index"), rs.getString("section_title"), rs.getString("source_json"),
                        rs.getString("text_content"), rs.getString("knowledge_json")), id, index).stream().findFirst().orElseThrow();
    }
    @Transactional
    public void saveBlocks(String id, List<Block> blocks, String warnings) {
        insertBlocks(id, blocks);
        jdbc.update("UPDATE document_files SET total_blocks=?,parse_warnings=?,parse_complete=TRUE,stage='READY',status='COMPLETED',error_message=NULL,updated_at=CURRENT_TIMESTAMP(3) WHERE id=?", blocks.size(), warnings, id);
    }
    /** Batch data and its resume cursor commit together. Retrieval reads committed rows. */
    @Transactional
    public void appendBatch(String id, int expectedBatch, List<Block> blocks, int parsedPages, int totalPages, String warnings) {
        int claimed = jdbc.update("""
                UPDATE document_files SET total_blocks=total_blocks+?,parsed_pages=?,total_pages=?,parse_batches=parse_batches+1,
                parse_warnings=?,stage='PIPELINING',updated_at=CURRENT_TIMESTAMP(3)
                WHERE id=? AND parse_batches=? AND parse_complete=FALSE AND status<>'CANCELLED'
                """, blocks.size(), parsedPages, totalPages, warnings, id, expectedBatch);
        if (claimed != 1) throw new IllegalStateException("解析批次检查点发生冲突");
        insertBlocks(id, blocks);
    }
    private void insertBlocks(String id, List<Block> blocks) {
        if (blocks.isEmpty()) return;
        jdbc.batchUpdate("INSERT INTO document_blocks(document_id,block_index,section_title,source_json,text_content) VALUES(?,?,?,?,?)",
                blocks, 100, (statement, block) -> {
                    statement.setString(1, id); statement.setInt(2, block.index()); statement.setString(3, block.section());
                    statement.setString(4, block.sourceJson()); statement.setString(5, block.text());
                });
    }
    public void parserProgress(String id, int totalPages) {
        jdbc.update("UPDATE document_files SET total_pages=?,updated_at=CURRENT_TIMESTAMP(3) WHERE id=?", totalPages, id);
    }
    public void parsed(String id) {
        if (get(id).totalBlocks() == 0) throw new IllegalArgumentException("文档没有可检索的文本内容");
        jdbc.update("UPDATE document_files SET parse_complete=TRUE,stage='READY',status='COMPLETED',error_message=NULL,updated_at=CURRENT_TIMESTAMP(3) WHERE id=? AND status<>'CANCELLED'", id);
    }
    public List<Block> chunks(String id, int offset, int size) {
        return jdbc.query("SELECT block_index,section_title,source_json,text_content FROM document_blocks WHERE document_id=? ORDER BY block_index LIMIT ? OFFSET ?",
                (rs, n) -> new Block(rs.getInt("block_index"), rs.getString("section_title"), rs.getString("source_json"), rs.getString("text_content"), null), id, size, offset);
    }
    @Transactional
    public void knowledge(String id, int index, JsonNode knowledge) {
        int changed = jdbc.update("UPDATE document_blocks SET knowledge_json=? WHERE document_id=? AND block_index=? AND knowledge_json IS NULL",
                knowledge.toString(), id, index);
        if (changed == 1) {
            jdbc.update("UPDATE document_files SET completed_blocks=completed_blocks+1,updated_at=CURRENT_TIMESTAMP(3) WHERE id=?", id);
            jdbc.update("UPDATE document_knowledge_failures SET resolved_at=CURRENT_TIMESTAMP(3) WHERE document_id=? AND block_index=? AND resolved_at IS NULL", id, index);
        }
    }
    public List<KnowledgeJob> pendingKnowledge() {
        return jdbc.query("""
                SELECT b.document_id,b.block_index,f.user_id FROM document_blocks b
                JOIN document_files f ON f.id=b.document_id
                WHERE f.status='PROCESSING' AND b.knowledge_pending=TRUE AND b.knowledge_attempts<3
                AND (b.knowledge_attempts=0 OR b.knowledge_updated_at<DATE_SUB(CURRENT_TIMESTAMP(3),INTERVAL 30 SECOND))
                ORDER BY b.block_index,f.created_at LIMIT 30
                """, (rs, n) -> new KnowledgeJob(rs.getString(1), rs.getInt(2), rs.getLong(3)));
    }
    @Transactional
    public void knowledgeFailed(String id, int index, ModelFailure failure) {
        int changed = jdbc.update("""
                UPDATE document_blocks SET knowledge_attempts=knowledge_attempts+1,knowledge_error=?,knowledge_error_type=?,
                knowledge_updated_at=CURRENT_TIMESTAMP(3) WHERE document_id=? AND block_index=? AND knowledge_json IS NULL
                """, failure.getMessage(), failure.type().name(), id, index);
        if (changed == 1) knowledgeFailure(id, index, failure, true);
    }
    public void knowledgeFailure(String id, int index, ModelFailure failure, boolean terminal) {
        jdbc.update("""
                INSERT INTO document_knowledge_failures(document_id,block_index,error_type,phase,error_message,http_status,terminal)
                VALUES(?,?,?,?,?,?,?)
                """, id, index, failure.type().name(), failure.phase(), failure.type().message, failure.httpStatus(), terminal);
    }
    public List<FailureEvent> failures(String id, int limit, Long beforeId) {
        return jdbc.query("""
                SELECT * FROM document_knowledge_failures WHERE document_id=? AND id<? ORDER BY id DESC LIMIT ?
                """, (rs, n) -> new FailureEvent(rs.getLong("id"), rs.getInt("block_index"), rs.getString("error_type"),
                rs.getString("phase"), rs.getString("error_message"), (Integer) rs.getObject("http_status"), rs.getBoolean("terminal"),
                rs.getTimestamp("created_at").toLocalDateTime(), rs.getTimestamp("resolved_at") == null ? null : rs.getTimestamp("resolved_at").toLocalDateTime()),
                id, beforeId == null ? Long.MAX_VALUE : beforeId, Math.max(1, Math.min(200, limit)));
    }
    public record FailureEvent(long id, int blockIndex, String errorType, String phase, String message, Integer httpStatus,
                               boolean terminal, java.time.LocalDateTime createdAt, java.time.LocalDateTime resolvedAt) {}
    public boolean exhaustedKnowledge(String id) {
        return jdbc.queryForObject("SELECT COUNT(*) FROM document_blocks WHERE document_id=? AND knowledge_json IS NULL AND knowledge_attempts>=3", Integer.class, id) > 0;
    }
    public List<String> readyToFinalize() {
        return jdbc.queryForList("""
                SELECT f.id FROM document_files f WHERE f.status='PROCESSING' AND f.parse_complete=TRUE
                AND (f.completed_blocks=f.total_blocks OR EXISTS(SELECT 1 FROM document_blocks b WHERE b.document_id=f.id AND b.knowledge_attempts>=3 AND b.knowledge_json IS NULL))
                ORDER BY f.created_at LIMIT 20
                """, String.class);
    }
    public record KnowledgeJob(String documentId, int blockIndex, Long userId) {}
    public void complete(String id, JsonNode report) {
        jdbc.update("UPDATE document_files SET status='COMPLETED',stage='COMPLETED',report_json=?,error_message=NULL,updated_at=CURRENT_TIMESTAMP(3) WHERE id=?",
                report.toString(), id);
    }
    public void failed(String id, String error, boolean retry) {
        jdbc.update("UPDATE document_files SET status=?,error_message=?,updated_at=CURRENT_TIMESTAMP(3) WHERE id=?",
                retry ? "RETRYING" : "FAILED", error, id);
    }
    public List<String> recoverable() {
        // Persistent queue intent is the outbox. Re-send until a worker claims the document.
        return jdbc.queryForList("""
                SELECT id FROM document_files WHERE
                (status IN ('QUEUED','RETRYING') AND updated_at < DATE_SUB(CURRENT_TIMESTAMP(3), INTERVAL 10 SECOND))
                OR (status='PROCESSING' AND parse_complete=FALSE AND updated_at < DATE_SUB(CURRENT_TIMESTAMP(3), INTERVAL 2 MINUTE))
                ORDER BY updated_at LIMIT 20
                """, String.class);
    }
    public record Block(int index, String section, String sourceJson, String text, String knowledgeJson) {}

    public List<ChatTurn> chatHistory(String id) {
        return jdbc.query("SELECT request_id,question,answer_json FROM document_chat_turns WHERE document_id=? ORDER BY id DESC LIMIT 30",
                (rs, n) -> new ChatTurn(rs.getString("request_id"), rs.getString("question"), rs.getString("answer_json")), id)
                .reversed();
    }
    public String chatAnswer(String id, String requestId) {
        return jdbc.queryForList("SELECT answer_json FROM document_chat_turns WHERE document_id=? AND request_id=?",
                String.class, id, requestId).stream().findFirst().orElse(null);
    }
    public void saveChat(String id, String requestId, String question, JsonNode answer) {
        jdbc.update("INSERT INTO document_chat_turns(document_id,request_id,question,answer_json) VALUES(?,?,?,?)",
                id, requestId, question, answer.toString());
    }
    public record ChatTurn(String requestId, String question, String answerJson) {}
}
