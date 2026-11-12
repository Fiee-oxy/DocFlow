CREATE TABLE document_adaptive_state (
 document_id VARCHAR(36) PRIMARY KEY,
 owner_id BIGINT NOT NULL,
 index_version VARCHAR(64) NOT NULL,
 token_count BIGINT NOT NULL DEFAULT 0,
 counting_mode VARCHAR(40) NOT NULL,
 processing_strategy VARCHAR(32) NOT NULL,
 index_requested BOOLEAN NOT NULL DEFAULT FALSE,
 indexed_blocks INT NOT NULL DEFAULT 0,
 indexed_batches INT NOT NULL DEFAULT 0,
 state VARCHAR(32) NOT NULL DEFAULT 'PARSING',
 error_message VARCHAR(500) NULL,
 index_attempts INT NOT NULL DEFAULT 0,
 next_retry_at TIMESTAMP(3) NULL,
 updated_at TIMESTAMP(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
 KEY idx_adaptive_work (index_requested,state,updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE document_parse_batches (
 document_id VARCHAR(36) NOT NULL,
 batch_index INT NOT NULL,
 payload LONGTEXT NOT NULL,
 PRIMARY KEY(document_id,batch_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE document_sections (
 document_id VARCHAR(36) NOT NULL,
 section_id VARCHAR(64) NOT NULL,
 parent_id VARCHAR(64) NULL,
 title VARCHAR(512) NOT NULL,
 title_path VARCHAR(2048) NOT NULL,
 section_order INT NOT NULL,
 start_page INT NULL,
 end_page INT NULL,
 PRIMARY KEY(document_id,section_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE document_rag_chunks (
 chunk_id VARCHAR(64) PRIMARY KEY,
 document_id VARCHAR(36) NOT NULL,
 index_version VARCHAR(64) NOT NULL,
 section_id VARCHAR(64) NOT NULL,
 block_index INT NOT NULL,
 content MEDIUMTEXT NOT NULL,
 search_text MEDIUMTEXT NOT NULL,
 source_json MEDIUMTEXT NOT NULL,
 token_count INT NOT NULL,
 page_start INT NULL,
 page_end INT NULL,
 KEY idx_rag_scope(document_id,index_version,block_index),
 KEY idx_rag_section(document_id,index_version,section_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE document_query_tasks (
 id VARCHAR(36) PRIMARY KEY,
 owner_id BIGINT NOT NULL,
 scope_key VARCHAR(64) NOT NULL,
 document_ids TEXT NOT NULL,
 question TEXT NOT NULL,
 request_hash VARCHAR(64) NOT NULL,
 state VARCHAR(32) NOT NULL,
 completed_steps INT NOT NULL DEFAULT 0,
 total_steps INT NOT NULL DEFAULT 0,
 result_json LONGTEXT NULL,
 error_message VARCHAR(500) NULL,
 cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
 created_at TIMESTAMP(3) DEFAULT CURRENT_TIMESTAMP(3),
 updated_at TIMESTAMP(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
 KEY idx_query_owner(owner_id,created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE document_analysis_cache (
 cache_key VARCHAR(64) PRIMARY KEY,
 owner_id BIGINT NOT NULL,
 result_json LONGTEXT NOT NULL,
 created_at TIMESTAMP(3) DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE document_query_events (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 task_id VARCHAR(36) NOT NULL,
 event_type VARCHAR(32) NOT NULL,
 payload MEDIUMTEXT NOT NULL,
 KEY idx_query_events(task_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
