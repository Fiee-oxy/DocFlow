package com.example.server.document.adaptive;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.core.type.TypeReference;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import java.util.Map;

/** First writer freezes settings for an index version; retries read that same snapshot. */
@Component
public class ChunkConfigurationStore {
    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;
    public ChunkConfigurationStore(JdbcTemplate jdbc,ObjectMapper mapper){this.jdbc=jdbc;this.mapper=mapper;}
    public Map<String,Object> freeze(AdaptiveDocumentRepository.State state,Map<String,Object> proposed)throws Exception{
        // Existing partial versions predate this feature and must retain legacy chunking.
        var chosen=new java.util.LinkedHashMap<>(proposed);
        if(state.indexedBatches()>0){chosen.put("strategy","legacy");chosen.put("hierarchy_enabled",false);if(chosen.containsKey("strategy_revision"))chosen.put("strategy_revision","legacy-v1");}
        jdbc.update("INSERT IGNORE INTO document_chunk_configuration(document_id,index_version,configuration_json) VALUES(?,?,?)",
            state.documentId(),state.version(),mapper.writeValueAsString(chosen));
        return mapper.readValue(jdbc.queryForObject("SELECT configuration_json FROM document_chunk_configuration WHERE document_id=? AND index_version=?",
            String.class,state.documentId(),state.version()),new TypeReference<Map<String,Object>>(){});
    }
}
