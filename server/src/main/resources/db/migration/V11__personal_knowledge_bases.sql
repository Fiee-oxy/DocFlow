CREATE TABLE knowledge_bases (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    name VARCHAR(100) NOT NULL,
    source_module VARCHAR(64) NOT NULL DEFAULT 'personal',
    default_slot TINYINT NULL,
    created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uq_default_knowledge_base (owner_id,default_slot),
    KEY idx_knowledge_base_owner (owner_id,created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE knowledge_documents (
    document_id VARCHAR(36) NOT NULL PRIMARY KEY,
    knowledge_base_id VARCHAR(36) NOT NULL,
    owner_id BIGINT NOT NULL,
    logical_document_id VARCHAR(36) NOT NULL,
    version_number INT NOT NULL DEFAULT 1,
    created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uq_document_revision (logical_document_id,version_number),
    KEY idx_knowledge_documents_scope (knowledge_base_id,owner_id),
    CONSTRAINT fk_kd_base FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_bases(id),
    CONSTRAINT fk_kd_document FOREIGN KEY (document_id) REFERENCES document_files(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO knowledge_bases(id,owner_id,name,source_module,default_slot)
SELECT CONCAT('personal-',user_id),user_id,'我的知识库','personal',1
FROM document_files GROUP BY user_id;

INSERT INTO knowledge_documents(document_id,knowledge_base_id,owner_id,logical_document_id,version_number)
SELECT id,CONCAT('personal-',user_id),user_id,id,1 FROM document_files;
