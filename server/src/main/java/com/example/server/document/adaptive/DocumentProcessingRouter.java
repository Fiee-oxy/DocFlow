package com.example.server.document.adaptive;
import org.springframework.stereotype.Component;
@Component
public class DocumentProcessingRouter {
    private final AdaptiveSettings settings;
    public DocumentProcessingRouter(AdaptiveSettings settings) { this.settings = settings; }
    public String indexVersion(){return settings.indexVersion();}
    public int indexBatchSize(){return settings.indexBatchSize();}
    public ProcessingStrategy route(long tokens) {
        if (tokens < 0) throw new IllegalArgumentException("Token count cannot be negative");
        if (tokens <= settings.shortTokens()) return ProcessingStrategy.DIRECT;
        if (tokens <= settings.mediumTokens()) return ProcessingStrategy.HYBRID_DECISION;
        if (tokens <= settings.largeTokens()) return ProcessingStrategy.STANDARD_RAG;
        return ProcessingStrategy.LARGE_DOCUMENT_RAG;
    }
}
