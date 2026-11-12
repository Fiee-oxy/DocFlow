package com.example.server.document;

import java.time.LocalDateTime;

public record DocumentRecord(String id, Long userId, String filename, long sizeBytes, int totalParts,
                             String sourcePath, String status, String stage, int totalBlocks,
                             int completedBlocks, int attempts, String errorMessage, String reportJson, String warningsJson,
                             LocalDateTime createdAt, int totalPages, int parsedPages, boolean parseComplete, int parseBatches) {
    public DocumentRecord(String id, Long userId, String filename, long sizeBytes, int totalParts,
                          String sourcePath, String status, String stage, int totalBlocks,
                          int completedBlocks, int attempts, String errorMessage, String reportJson, String warningsJson,
                          LocalDateTime createdAt) {
        this(id, userId, filename, sizeBytes, totalParts, sourcePath, status, stage, totalBlocks,
                completedBlocks, attempts, errorMessage, reportJson, warningsJson, createdAt,
                0, 0, totalBlocks > 0, 0);
    }
    public View view() {
        return new View(id, filename, sizeBytes, status, stage, totalBlocks, completedBlocks,
                errorMessage, createdAt, totalPages, parsedPages, parseComplete);
    }
    public record View(String id, String filename, long sizeBytes, String status, String stage,
                       int totalBlocks, int completedBlocks, String errorMessage, LocalDateTime createdAt,
                       int totalPages, int parsedPages, boolean parseComplete) {}
}
