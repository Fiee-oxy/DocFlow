package com.example.server.document.adaptive;

import com.fasterxml.jackson.databind.*;
import org.springframework.stereotype.Repository;
import org.springframework.jdbc.core.JdbcTemplate;
import java.util.*;

@Repository
public class AdaptiveQueryTaskStore {
    private final JdbcTemplate jdbc;private final ObjectMapper mapper;
    public AdaptiveQueryTaskStore(JdbcTemplate jdbc,ObjectMapper mapper){this.jdbc=jdbc;this.mapper=mapper;}
    public boolean create(String id,Long owner,List<String> docs,String question)throws Exception{
        String hash=AdaptiveDocumentRepository.hash(mapper.writeValueAsString(Map.of("documents",docs,"question",question)));
        int changed=jdbc.update("INSERT IGNORE INTO document_query_tasks(id,owner_id,scope_key,document_ids,question,request_hash,state) VALUES(?,?,?,?,?,?,'QUEUED')",
                id,owner,AdaptiveDocumentRepository.hash(docs.toString()),mapper.writeValueAsString(docs),question,hash);
        var saved=owned(id,owner);
        if(!hash.equals(saved.path("request_hash").asText()))throw new IllegalArgumentException("同一 requestId 不得用于不同问题或文档范围");
        return changed==1;
    }
    public JsonNode owned(String id,Long owner){
        var rows=jdbc.queryForList("SELECT * FROM document_query_tasks WHERE id=?",id);
        if(rows.isEmpty())throw new NoSuchElementException("问答任务不存在");
        var row=rows.getFirst();if(((Number)row.get("owner_id")).longValue()!=owner)throw new SecurityException("无权访问该问答任务");
        var result=mapper.valueToTree(row);return result;
    }
    public boolean begin(String id){return jdbc.update("UPDATE document_query_tasks SET state='RUNNING' WHERE id=? AND state='QUEUED' AND cancel_requested=FALSE",id)==1;}
    public List<Map<String,Object>> pending(){return jdbc.queryForList("SELECT id,owner_id,document_ids,question FROM document_query_tasks WHERE state='QUEUED' AND cancel_requested=FALSE ORDER BY created_at LIMIT 32");}
    public boolean cancelled(String id){return Boolean.TRUE.equals(jdbc.queryForObject("SELECT cancel_requested FROM document_query_tasks WHERE id=?",Boolean.class,id));}
    public void cancel(String id){jdbc.update("UPDATE document_query_tasks SET cancel_requested=TRUE,state=IF(state='QUEUED','CANCELLED',state) WHERE id=?",id);}
    public void progress(String id,int done,int total){jdbc.update("UPDATE document_query_tasks SET completed_steps=?,total_steps=? WHERE id=?",done,total,id);}
    public void complete(String id,JsonNode answer){jdbc.update("UPDATE document_query_tasks SET state='COMPLETED',result_json=? WHERE id=?",answer.toString(),id);}
    public void fail(String id,boolean cancel,String message){jdbc.update("UPDATE document_query_tasks SET state=?,error_message=? WHERE id=?",cancel?"CANCELLED":"FAILED",message,id);}
    public void failureDetails(String id,String type,String phase,Integer status){jdbc.update("UPDATE document_query_tasks SET failure_type=?,failure_phase=?,failure_http_status=? WHERE id=?",type,phase,status,id);}
    public void modelStarted(String id){jdbc.update("UPDATE document_query_tasks SET llm_calls=llm_calls+1,usage_unknown=TRUE WHERE id=?",id);}
    public void usage(String id,Map<String,Object> usage){
        long input=((Number)usage.getOrDefault("input_tokens",-1)).longValue(),output=((Number)usage.getOrDefault("output_tokens",-1)).longValue();
        int known=input>=0&&output>=0?1:0;
        jdbc.update("UPDATE document_query_tasks SET input_tokens=input_tokens+?,output_tokens=output_tokens+?,usage_unknown=(usage_known_calls+?<llm_calls),usage_known_calls=usage_known_calls+? WHERE id=?",Math.max(0,input),Math.max(0,output),known,known,id);
    }
    public void event(String id,String type,Object data)throws Exception{
        jdbc.update("INSERT INTO document_query_events(task_id,event_type,payload) VALUES(?,?,?)",id,type,mapper.writeValueAsString(data));
        jdbc.update("UPDATE document_query_tasks SET updated_at=CURRENT_TIMESTAMP(3) WHERE id=?",id);
    }
    public List<Map<String,Object>> events(String id,long after){return jdbc.queryForList("SELECT id,event_type,payload FROM document_query_events WHERE task_id=? AND id>? ORDER BY id LIMIT 100",id,after);}
    @org.springframework.scheduling.annotation.Scheduled(fixedDelay=60000,initialDelay=10000)
    public void recoverInterrupted(){
        jdbc.update("UPDATE document_query_tasks SET state='FAILED',error_message='任务因服务中断停止，已完成分析缓存保留；请重新提交' WHERE state IN ('QUEUED','RUNNING') AND updated_at<DATE_SUB(CURRENT_TIMESTAMP(3),INTERVAL 10 MINUTE)");
    }
    @org.springframework.scheduling.annotation.Scheduled(fixedDelay=3600000,initialDelay=3600000)
    public void cleanup(){
        jdbc.update("DELETE e FROM document_query_events e JOIN document_query_tasks t ON e.task_id=t.id WHERE t.state IN ('COMPLETED','FAILED','CANCELLED') AND t.updated_at<DATE_SUB(CURRENT_TIMESTAMP(3),INTERVAL 1 DAY)");
        jdbc.update("DELETE FROM document_analysis_cache WHERE created_at<DATE_SUB(CURRENT_TIMESTAMP(3),INTERVAL 7 DAY)");
    }
}
