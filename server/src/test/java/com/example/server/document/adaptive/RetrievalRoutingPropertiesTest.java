package com.example.server.document.adaptive;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class RetrievalRoutingPropertiesTest {
    @Test void validatesThresholdsAndUnboundedShadow(){
        var p=new RetrievalRoutingProperties();assertDoesNotThrow(p::validate);assertTrue(p.lowConfidenceFallback());
        p.getFallback().setDenseMinScore(Double.NaN);assertThrows(IllegalArgumentException.class,p::validate);
        p.getFallback().setDenseMinScore(.45);p.getFallback().setBm25MinCoverage(1.1);assertThrows(IllegalArgumentException.class,p::validate);
        p.getFallback().setBm25MinCoverage(.55);p.getFallback().setQualityTopK(0);assertThrows(IllegalArgumentException.class,p::validate);
        p.getFallback().setQualityTopK(5);p.getShadow().setQueueCapacity(100);assertThrows(IllegalArgumentException.class,p::validate);
        p.getShadow().setQueueCapacity(4);p.setEnabled(true);p.setMode(RetrievalRoutingProperties.RunMode.SHADOW);assertThrows(IllegalArgumentException.class,p::validate);
        p.getShadow().setUrl("http://isolated:5002");assertDoesNotThrow(p::validate);
    }
    @Test void lowConfidenceSwitchIsIndependentAndThresholdsInvalidateCacheIdentity(){
        var p=new RetrievalRoutingProperties();p.getFallback().setOnEmpty(false);
        assertFalse(p.emptyFallback());assertTrue(p.lowConfidenceFallback());assertEquals(true,p.fallbackParameters().get("fallback_enabled"));
        var before=p.identity();p.getFallback().setDenseMinScore(.6);assertNotEquals(before,p.identity());
        p.getFallback().setMaxAttempts(0);assertFalse(p.lowConfidenceFallback());assertEquals(false,p.fallbackParameters().get("fallback_enabled"));
    }
    @Test void disabledOrBaselineAlwaysUsesOriginalPath(){
        var p=new RetrievalRoutingProperties();assertFalse(p.routed());p.setEnabled(true);assertTrue(p.routed());
        p.setMode(RetrievalRoutingProperties.RunMode.BASELINE);assertFalse(p.routed());assertTrue(p.emptyFallback());
        p.getFallback().setMaxAttempts(0);assertFalse(p.emptyFallback());
    }
    @Test void bindsNestedSpringConfiguration(){
        new org.springframework.boot.test.context.runner.ApplicationContextRunner()
            .withConfiguration(org.springframework.boot.autoconfigure.AutoConfigurations.of(org.springframework.boot.autoconfigure.context.ConfigurationPropertiesAutoConfiguration.class))
            .withUserConfiguration(RetrievalRoutingProperties.class)
            .withPropertyValues("document.retrieval.routing.enabled=true","document.retrieval.routing.mode=BASELINE","document.retrieval.routing.fallback.enabled=false",
                "document.retrieval.routing.fallback.dense-min-score=0.6","document.retrieval.routing.fallback.bm25-min-coverage=0.7")
            .run(context->{assertNull(context.getStartupFailure());var p=context.getBean(RetrievalRoutingProperties.class);assertTrue(p.isEnabled());assertFalse(p.routed());assertFalse(p.emptyFallback());
                assertEquals(.6,p.fallbackParameters().get("dense_min_score"));assertEquals(.7,p.fallbackParameters().get("bm25_min_coverage"));});
    }
}
