-- Preserve source chunks and historical model outputs; completed parsing is enough for RAG.
UPDATE document_files SET status='COMPLETED',stage='READY',error_message=NULL,updated_at=CURRENT_TIMESTAMP(3)
WHERE parse_complete=TRUE AND total_blocks>0 AND status<>'UPLOADING';
