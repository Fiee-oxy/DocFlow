package com.example.server.document.adaptive;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@ConfigurationProperties("document.adaptive")
public record AdaptiveSettings(long shortTokens, long mediumTokens, long largeTokens, int contextTokens,
        int outputTokens, int safetyTokens, int factTokens, int localTokens, int comparisonTokens, int mapTokens,
        int denseTopK, int sparseTopK, int fusionTopK, int finalTopK, int rrfK, int maxDocuments,
        int maxGlobalCalls, int indexBatchSize, String retrievalUrl, String retrievalKey, String indexVersion) {
    public AdaptiveSettings {
        if (shortTokens < 1 || mediumTokens <= shortTokens || largeTokens <= mediumTokens
                || contextTokens <= outputTokens + safetyTokens || outputTokens < 1 || safetyTokens < 0
                || factTokens < 1 || localTokens < 1 || comparisonTokens < 1 || mapTokens < 1
                || denseTopK < 1 || denseTopK>100 || sparseTopK < 1 || sparseTopK>100 || fusionTopK < 1 || fusionTopK>100 || finalTopK < 1 || finalTopK>fusionTopK || rrfK < 1
                || maxDocuments < 2 || maxDocuments>64 || maxGlobalCalls < 2 || indexBatchSize < 1 || indexBatchSize>100 || indexVersion==null || indexVersion.isBlank() || indexVersion.length()>64)
            throw new IllegalArgumentException("Invalid adaptive document configuration");
    }
    @Configuration @EnableConfigurationProperties(AdaptiveSettings.class)
    public static class Registration {}
}
