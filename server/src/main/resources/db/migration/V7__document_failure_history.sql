ALTER TABLE document_blocks ADD COLUMN knowledge_error_type VARCHAR(40) NULL;
CREATE TABLE document_knowledge_failures (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    document_id VARCHAR(36) NOT NULL,
    block_index INT NOT NULL,
    error_type VARCHAR(40) NOT NULL,
    phase VARCHAR(24) NOT NULL,
    error_message VARCHAR(255) NOT NULL,
    http_status INT NULL,
    terminal BOOLEAN NOT NULL,
    created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    resolved_at TIMESTAMP(3) NULL,
    KEY idx_failure_document (document_id, id),
    KEY idx_failure_block (document_id, block_index, resolved_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
-- Preserve evidence of earlier retries without inventing their causes or exact retry counts.
INSERT INTO document_knowledge_failures(document_id,block_index,error_type,phase,error_message,terminal,created_at,resolved_at)
SELECT document_id,block_index,'LEGACY_UNKNOWN','LEGACY','旧版本有重试记录，但未保存具体失败原因',TRUE,
       knowledge_updated_at,IF(knowledge_json IS NOT NULL,CURRENT_TIMESTAMP(3),NULL)
FROM document_blocks WHERE knowledge_attempts>0;
UPDATE document_blocks SET knowledge_error_type='LEGACY_UNKNOWN' WHERE knowledge_attempts>0;
