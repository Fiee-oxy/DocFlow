package com.example.server.document;

import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.springframework.stereotype.Component;
import org.springframework.beans.factory.annotation.Value;

import java.util.UUID;
import java.util.concurrent.Semaphore;

@Component
@RocketMQMessageListener(topic = "${rocketmq.topic.document-analysis:document-analysis-topic}",
        consumerGroup = "${rocketmq.consumer.document-group:document-analysis-group}", maxReconsumeTimes = 2)
public class DocumentConsumer implements RocketMQListener<String> {
    private final DocumentWorkflow workflow;
    private final Semaphore workers;
    public DocumentConsumer(DocumentWorkflow workflow, @Value("${document.dispatch-workers:4}") int concurrency) {
        this.workflow = workflow;
        this.workers = new Semaphore(Math.max(1, concurrency));
    }
    @Override public void onMessage(String id) {
        try { UUID.fromString(id); } catch (Exception invalid) { return; }
        // Local bound; durable queue recovery will re-send a document if delivery expires while busy.
        if (!workers.tryAcquire()) throw new IllegalStateException("文档处理线程繁忙");
        try { workflow.process(id); }
        finally { workers.release(); }
    }
}
