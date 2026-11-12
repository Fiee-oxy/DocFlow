CREATE TABLE document_chat_turns (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    document_id VARCHAR(36) NOT NULL,
    request_id VARCHAR(36) NOT NULL,
    question TEXT NOT NULL,
    answer_json MEDIUMTEXT NOT NULL,
    created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uq_document_chat_request (document_id, request_id),
    KEY idx_document_chat_history (document_id, id),
    CONSTRAINT fk_document_chat_file FOREIGN KEY (document_id) REFERENCES document_files(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
