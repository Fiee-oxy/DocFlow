CREATE TABLE document_files (
    id VARCHAR(36) NOT NULL,
    user_id BIGINT NOT NULL,
    filename VARCHAR(255) NOT NULL,
    size_bytes BIGINT NOT NULL,
    total_parts INT NOT NULL,
    source_path VARCHAR(1024) NULL,
    content_hash VARCHAR(64) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'UPLOADING',
    stage VARCHAR(32) NOT NULL DEFAULT 'UPLOADING',
    total_blocks INT NOT NULL DEFAULT 0,
    completed_blocks INT NOT NULL DEFAULT 0,
    attempts INT NOT NULL DEFAULT 0,
    error_message VARCHAR(1000) NULL,
    parse_warnings TEXT NULL,
    report_json LONGTEXT NULL,
    created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_document_user (user_id, created_at),
    KEY idx_document_recovery (status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE document_parts (
    document_id VARCHAR(36) NOT NULL,
    part_index INT NOT NULL,
    size_bytes BIGINT NOT NULL,
    PRIMARY KEY (document_id, part_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE document_blocks (
    document_id VARCHAR(36) NOT NULL,
    block_index INT NOT NULL,
    section_title VARCHAR(512) NOT NULL,
    source_json MEDIUMTEXT NOT NULL,
    text_content MEDIUMTEXT NOT NULL,
    knowledge_json MEDIUMTEXT NULL,
    PRIMARY KEY (document_id, block_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
