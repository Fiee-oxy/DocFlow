package com.example.server.document.adaptive;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;
import org.springframework.validation.annotation.Validated;
import jakarta.annotation.PostConstruct;
import java.util.*;

/** Top-K remains in AdaptiveSettings; routing must not create a second ranking configuration. */
@Data @Component @Validated @ConfigurationProperties(prefix="document.retrieval.routing")
public class RetrievalRoutingProperties {
    public enum RunMode { BASELINE, ROUTED, SHADOW }
    private boolean enabled=false;
    private RunMode mode=RunMode.ROUTED;
    private RetrievalMode defaultMode=RetrievalMode.HYBRID;
    private Map<String,Boolean> rules=new LinkedHashMap<>();
    private Fallback fallback=new Fallback();
    private Shadow shadow=new Shadow();
    private boolean metricsEnabled=true;
    @Data public static class Fallback {
        private boolean enabled=true,onEmpty=true,onLowConfidence=true;
        private int maxAttempts=1;
        private double bm25MinCoverage=.55,denseMinScore=.45;
        private int qualityTopK=5;
    }
    @Data public static class Shadow {
        private String url="";
        private double sampleRate=.01;
        private int workers=1,queueCapacity=4,timeoutSeconds=30;
    }
    @PostConstruct public void validate(){
        if(mode==null||fallback==null||shadow==null||rules==null||defaultMode!=RetrievalMode.HYBRID)throw new IllegalArgumentException("Ambiguous requests must default to HYBRID");
        if(fallback.maxAttempts<0||fallback.maxAttempts>1 || !Double.isFinite(fallback.bm25MinCoverage) ||
                fallback.bm25MinCoverage<0||fallback.bm25MinCoverage>1||!Double.isFinite(fallback.denseMinScore)||
                fallback.denseMinScore < -1||fallback.denseMinScore>1||fallback.qualityTopK<1||fallback.qualityTopK>20)
            throw new IllegalArgumentException("Invalid confidence thresholds or fallback attempt budget");
        if(shadow.sampleRate<0||shadow.sampleRate>1||!Double.isFinite(shadow.sampleRate)||shadow.workers<1||shadow.workers>2||shadow.queueCapacity<1||shadow.queueCapacity>32||shadow.timeoutSeconds<1||shadow.timeoutSeconds>120)
            throw new IllegalArgumentException("Invalid shadow resource budget");
        if(enabled&&mode==RunMode.SHADOW&&(!shadow.url.startsWith("http://")&&!shadow.url.startsWith("https://")))
            throw new IllegalArgumentException("SHADOW requires an isolated retrieval URL");
        if(!Set.of("general-knowledge","metadata","section-read","exact-lookup","semantic-similarity").containsAll(rules.keySet()))
            throw new IllegalArgumentException("Unknown routing rule switch");
    }
    public boolean routed(){return enabled&&mode==RunMode.ROUTED;}
    public boolean emptyFallback(){return fallback.enabled&&fallback.onEmpty&&fallback.maxAttempts==1;}
    public boolean lowConfidenceFallback(){return fallback.enabled&&fallback.onLowConfidence&&fallback.maxAttempts==1;}
    public Map<String,Object> fallbackParameters(){return Map.of("fallback_enabled",emptyFallback()||lowConfidenceFallback(),
        "fallback_on_empty",fallback.onEmpty,"fallback_on_low_confidence",fallback.onLowConfidence,
        "bm25_min_coverage",fallback.bm25MinCoverage,"dense_min_score",fallback.denseMinScore,"quality_top_k",fallback.qualityTopK);}
    public Map<String,Object> identity(){return Map.of("enabled",enabled,"mode",mode.name(),"rules",rules,
        "emptyFallback",emptyFallback(),"fallback",fallbackParameters(),"qualityVersion","retrieval-quality-v1","version","retrieval-router-v2");}
}
