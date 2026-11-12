CREATE TABLE document_structure_batches (
 document_id VARCHAR(36) NOT NULL,
 index_version VARCHAR(64) NOT NULL,
 batch_index INT NOT NULL,
 structure_json JSON NOT NULL,
 PRIMARY KEY(document_id,index_version,batch_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE document_parent_chunks (
 parent_id VARCHAR(64) NOT NULL,
 document_id VARCHAR(36) NOT NULL,
 index_version VARCHAR(64) NOT NULL,
 batch_index INT NOT NULL,
 section_id VARCHAR(64) NOT NULL,
 content LONGTEXT NOT NULL,
 source_json JSON NOT NULL,
 child_ids JSON NOT NULL,
 token_count INT NOT NULL,
 PRIMARY KEY(document_id,index_version,parent_id),
 KEY idx_parent_batch(document_id,index_version,batch_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
