CREATE TABLE document_chunk_configuration (
 document_id VARCHAR(36) NOT NULL,
 index_version VARCHAR(64) NOT NULL,
 configuration_json TEXT NOT NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(document_id,index_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
