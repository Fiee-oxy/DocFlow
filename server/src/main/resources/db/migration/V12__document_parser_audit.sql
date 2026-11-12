-- Additive only: existing document tables and index versions are unchanged.
CREATE TABLE IF NOT EXISTS document_parser_audit (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  document_id VARCHAR(36) NOT NULL,
  event_type VARCHAR(32) NOT NULL,
  payload JSON NOT NULL,
  created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  INDEX idx_parser_audit_document(document_id, id),
  INDEX idx_parser_audit_created(created_at)
);
