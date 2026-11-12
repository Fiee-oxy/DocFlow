package com.example.server.document.adaptive;
import org.springframework.stereotype.Component;
import java.util.List;

@Component
public class QueryStrategyRouter {
    @org.springframework.beans.factory.annotation.Autowired(required=false) private RetrievalRoutingProperties retrievalRouting;
    public record QueryRoutingContext(String question, List<String> documentIds, long documentTokenCount,
            ProcessingStrategy processingStrategy, boolean indexReady, boolean partialIndexReady,
            int maxContextTokens, boolean parsingComplete) {}
    public record Decision(QueryStrategy strategy, String reason, boolean needsIndex) {}
    public boolean global(String q) {
        if(retrievalRouting!=null&&retrievalRouting.routed())return RoutingText.outer(q).matches("(?is).*(全文|整本|整个文档|全部文件|所有文件|整份|全文审查|核心贡献|full document|entire document|summarize the document).*" );
        return q.matches("(?is).*(全文|整本|整个文档|全部文件|所有文件|整份|全文审查|full document|entire document).*" );
    }
    public boolean section(String q) { return (retrievalRouting!=null&&retrievalRouting.routed()?RoutingText.outer(q):q).matches("(?is).*(第[一二三四五六七八九十百0-9]+[章节]|section\\s+\\d+|chapter\\s+\\d+).*" ); }
    public Decision route(QueryRoutingContext c) {
        if (c.documentIds() == null || c.documentIds().isEmpty()) throw new IllegalArgumentException("请选择文档");
        if (c.documentIds().size() > 1) return new Decision(QueryStrategy.MULTI_DOCUMENT, "AUTHORIZED_DOCUMENT_SET", true);
        if (global(c.question())) return new Decision(QueryStrategy.GLOBAL_ANALYSIS, "EXPLICIT_FULL_COVERAGE", false);
        if (section(c.question())) return new Decision(QueryStrategy.SECTION_RAG, "EXPLICIT_SECTION", true);
        boolean fits = c.parsingComplete() && c.documentTokenCount() <= c.maxContextTokens();
        if (fits && (c.processingStrategy() == ProcessingStrategy.DIRECT
                || c.processingStrategy() == ProcessingStrategy.HYBRID_DECISION && !c.indexReady()))
            return new Decision(QueryStrategy.DIRECT_CONTEXT, "COMPLETE_CONTEXT_WITHIN_BUDGET", c.processingStrategy() == ProcessingStrategy.HYBRID_DECISION);
        return new Decision(QueryStrategy.LOCAL_RAG, c.indexReady() ? "READY_INDEX_LOWER_CONTEXT_COST"
                : c.partialIndexReady() ? "EXPLICIT_PARTIAL_COVERAGE" : "WAIT_FOR_INDEX", !c.indexReady());
    }
}
