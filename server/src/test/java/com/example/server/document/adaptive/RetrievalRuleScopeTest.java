package com.example.server.document.adaptive;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class RetrievalRuleScopeTest {
    @Test void quotesAreDataAndScopeDoesNotImplyFullAnalysis(){
        var p=new RetrievalRoutingProperties();p.setEnabled(true);var router=new QueryStrategyRouter();
        org.springframework.test.util.ReflectionTestUtils.setField(router,"retrievalRouting",p);
        assertFalse(router.global("查找包含‘总结全文’的原文"));assertTrue(router.global("summarize the document"));
        assertFalse(router.section("Find exact \"Chapter 3\""));assertTrue(router.section("第三章中查找 Redis 原因"));
    }
}
