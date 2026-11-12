CREATE TABLE document_upload_integrity (
    document_id VARCHAR(36) NOT NULL PRIMARY KEY,
    expected_sha256 CHAR(64) NOT NULL,
    verification_state VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    expires_at TIMESTAMP(3) NOT NULL,
    verified_at TIMESTAMP(3) NULL,
    cleanup_pending BOOLEAN NOT NULL DEFAULT TRUE,
    KEY idx_upload_expiry (verification_state, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- One current upload per authenticated content/scope, locked transactionally at initialization.
CREATE TABLE document_upload_slots (
    scope_key CHAR(64) NOT NULL PRIMARY KEY,
    document_id VARCHAR(36) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE document_parts ADD COLUMN sha256 CHAR(64) NULL;
