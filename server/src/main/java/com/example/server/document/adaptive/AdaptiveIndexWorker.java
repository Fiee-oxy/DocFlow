package com.example.server.document.adaptive;

import org.redisson.api.RedissonClient;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.Semaphore;

@Component
public class AdaptiveIndexWorker {
    private final AdaptiveDocumentRepository repository;
    private final LocalRetrievalClient retrieval;
    private final RedissonClient redisson;
    private final Executor executor;
    private final Semaphore slot=new Semaphore(1);
    @org.springframework.beans.factory.annotation.Autowired(required=false)
    private com.example.server.document.DocumentRepository documents;
    public AdaptiveIndexWorker(AdaptiveDocumentRepository repository,LocalRetrievalClient retrieval,RedissonClient redisson,
            @Qualifier("documentIndexExecutor")Executor executor){this.repository=repository;this.retrieval=retrieval;this.redisson=redisson;this.executor=executor;}
    @Scheduled(fixedDelay=2000,initialDelay=10000)
    public void poll(){
        if(!retrieval.configured())return;
        for(String id:repository.pending()){
            if(!slot.tryAcquire())return;
            try{executor.execute(()->{try{process(id);}finally{slot.release();}});}
            catch(RejectedExecutionException e){slot.release();return;}
        }
    }
    @Scheduled(fixedDelay=10000,initialDelay=15000)
    public void classifyExisting(){
        for(var doc:repository.unclassified()){
            try{repository.bootstrap(doc.id(),doc.userId());}
            catch(Exception e){org.slf4j.LoggerFactory.getLogger(getClass()).warn("document_classification_failed documentId={}",doc.id());}
        }
    }
    public void process(String id){
        var lock=redisson.getLock("lock:document:adaptive-index:"+id);
        if(!lock.tryLock())return;
        try{
            if(documents!=null&&"CANCELLED".equals(documents.get(id).status()))return;
            var state=repository.state(id);if(state==null||state.ready())return;
            var batch=repository.nextBatch(state);
            if(batch==null){repository.publishReady(id);return;}
            var chunks=retrieval.index(state,batch);
            if(!lock.isHeldByCurrentThread())throw new IllegalStateException("Index lock lost");
            repository.indexed(state,chunks);repository.publishReady(id);
        }catch(java.util.concurrent.CancellationException cancelled){
            // Cancellation is terminal, not an indexing retry failure.
        }catch(Exception e){repository.indexFailure(id);
            org.slf4j.LoggerFactory.getLogger(getClass()).warn("document_index_failed documentId={} type={}",id,e.getClass().getSimpleName());
        }finally{if(lock.isHeldByCurrentThread())lock.unlock();}
    }
}
