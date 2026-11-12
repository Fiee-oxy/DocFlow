package com.example.server.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.util.concurrent.ThreadPoolExecutor;

@Configuration
@EnableAsync
public class ThreadPoolConfig {
    @Bean
    public org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler taskScheduler() {
        var scheduler=new org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler();
        scheduler.setPoolSize(4);scheduler.setThreadNamePrefix("Document-Schedule-");return scheduler;
    }

    @Bean("aiTaskExecutor")
    public ThreadPoolTaskExecutor aiTaskExecutor() {
        return executor("AI-Thread-", 4, 8, 100);
    }

    @Bean("asrExecutor")
    public ThreadPoolTaskExecutor asrExecutor() {
        return executor("ASR-Thread-", 4, 8, 50);
    }

    @Bean("ocrExecutor")
    public ThreadPoolTaskExecutor ocrExecutor() {
        int cores = Math.min(8, Math.max(1, Runtime.getRuntime().availableProcessors() / 2));
        return executor("OCR-Thread-", cores, cores, 20);
    }

    @Bean("modelCallExecutor")
    public ThreadPoolTaskExecutor modelCallExecutor() {
        return executor("LLM-Thread-", 4, 8, 20);
    }

    @Bean("documentChatExecutor")
    public ThreadPoolTaskExecutor documentChatExecutor(@Value("${document.chat-workers:2}") int workers) {
        int size = Math.max(1, Math.min(32, workers));
        return executor("Document-Chat-", size, size, 0);
    }

    @Bean("documentIndexExecutor")
    public ThreadPoolTaskExecutor documentIndexExecutor() {
        return executor("Document-Index-", 1, 1, 1);
    }
    @Bean("documentQueryExecutor")
    public ThreadPoolTaskExecutor documentQueryExecutor() {
        return executor("Document-Query-", 2, 2, 8);
    }
    @Bean("documentGlobalExecutor")
    public ThreadPoolTaskExecutor documentGlobalExecutor() {
        return executor("Document-Global-", 1, 1, 2);
    }

    private ThreadPoolTaskExecutor executor(String prefix, int coreSize, int maxSize, int queueCapacity) {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(coreSize);
        executor.setMaxPoolSize(maxSize);
        executor.setQueueCapacity(queueCapacity);
        executor.setThreadNamePrefix(prefix);
        executor.setRejectedExecutionHandler(new ThreadPoolExecutor.AbortPolicy());
        executor.setWaitForTasksToCompleteOnShutdown(true);
        executor.setAwaitTerminationSeconds(30);
        executor.initialize();
        return executor;
    }
}
