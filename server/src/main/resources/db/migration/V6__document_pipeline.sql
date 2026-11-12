ALTER TABLE document_files
    ADD COLUMN total_pages INT NOT NULL DEFAULT 0,
    ADD COLUMN parsed_pages INT NOT NULL DEFAULT 0,
    ADD COLUMN parse_complete BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN parse_batches INT NOT NULL DEFAULT 0;
UPDATE document_files SET parse_complete=TRUE WHERE total_blocks>0;
ALTER TABLE document_blocks
    ADD COLUMN knowledge_attempts INT NOT NULL DEFAULT 0,
    ADD COLUMN knowledge_error VARCHAR(1000) NULL,
    ADD COLUMN knowledge_updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    ADD COLUMN knowledge_pending BOOLEAN GENERATED ALWAYS AS (knowledge_json IS NULL) STORED,
    ADD INDEX idx_knowledge_pending (knowledge_pending, knowledge_attempts, knowledge_updated_at, document_id);
ALTER TABLE document_files ADD INDEX idx_document_finalize (status, parse_complete, created_at);
