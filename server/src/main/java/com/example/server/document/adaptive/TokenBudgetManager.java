package com.example.server.document.adaptive;

import org.springframework.stereotype.Component;
import java.nio.charset.StandardCharsets;

/** Conservative UTF-8 byte upper bound, NOT an exact model tokenizer. No paid calls. */
@Component
public class TokenBudgetManager {
    private final AdaptiveSettings settings;
    public TokenBudgetManager(AdaptiveSettings settings) { this.settings = settings; }
    public long count(String text) { return text == null ? 0 : text.getBytes(StandardCharsets.UTF_8).length; }
    public String countingMode() { return "UTF8_BYTE_UPPER_BOUND"; }
    public int available(String system, String question, String history) {
        long available = settings.contextTokens() - settings.outputTokens() - settings.safetyTokens()
                - count(system) - count(question) - count(history);
        if (available <= 0) throw new IllegalArgumentException("提示词、问题或历史已超出上下文预算");
        return (int) Math.min(Integer.MAX_VALUE, available);
    }
    public int evidenceBudget(QueryStrategy strategy, String question, int available) {
        int preferred = switch (strategy) {
            case DIRECT_CONTEXT -> available;
            case GLOBAL_ANALYSIS -> settings.mapTokens();
            case MULTI_DOCUMENT -> settings.comparisonTokens();
            default -> question.matches("(?s).*(比较|对比|差异|要求|条件|流程|步骤|课程|有哪些|包括哪些|列出|清单|汇总|归纳|总结|概述).*" ) ? settings.comparisonTokens()
                    : question.matches("(?s).*(解释|为什么|如何|怎么).*" ) ? settings.localTokens() : settings.factTokens();
        };
        return Math.min(available, preferred);
    }
}
