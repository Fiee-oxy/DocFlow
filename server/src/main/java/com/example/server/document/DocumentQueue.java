package com.example.server.document;

import org.apache.rocketmq.spring.core.RocketMQTemplate;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

@Service
public class DocumentQueue {
    private static final Logger log = LoggerFactory.getLogger(DocumentQueue.class);
    private final RocketMQTemplate mq;
    private final DocumentRepository repository;
    private final String topic;
    public DocumentQueue(RocketMQTemplate mq, DocumentRepository repository,
                         @Value("${rocketmq.topic.document-analysis:document-analysis-topic}") String topic) {
        this.mq = mq; this.repository = repository; this.topic = topic;
    }
    public void send(String id) {
        try { mq.convertAndSend(topic, id); }
        catch (RuntimeException e) {
            // Queue intent is already committed. Recovery retries without losing the upload.
            log.warn("document_dispatch_deferred documentId={}", id, e);
        }
    }
    @Scheduled(fixedDelayString = "${document.recovery-interval-ms:10000}", initialDelay = 10000)
    public void recover() {
        try { repository.recoverable().forEach(this::send); }
        catch (RuntimeException e) { log.warn("document_recovery_unavailable", e); }
    }
    @Configuration
    @EnableScheduling
    static class Scheduling {}
}
