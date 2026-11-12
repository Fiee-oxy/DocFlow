package com.example.server.document.adaptive;
import java.util.List;
public record RouteDecision(RetrievalMode mode,String reasonCode,List<String> matchedRules,RetrievalScope scope,
    boolean fallbackEnabled,String ruleVersion){}
