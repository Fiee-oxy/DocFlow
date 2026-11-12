package com.example.server.document.adaptive;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;
class ChunkConfigurationStoreTest {
 @Test void persistedConfigurationWinsOnRetry()throws Exception{
  var jdbc=mock(JdbcTemplate.class);
  when(jdbc.queryForObject(anyString(),eq(String.class),eq("d"),eq("v"))).thenReturn("{\"strategy\":\"boundary\",\"target_tokens\":350}");
  var store=new ChunkConfigurationStore(jdbc,new ObjectMapper());
  var state=new AdaptiveDocumentRepository.State("d",1L,"v",100,ProcessingStrategy.STANDARD_RAG,true,20,1,"PARTIAL_READY",null);
  var actual=store.freeze(state,Map.of("strategy","legacy","target_tokens",200));
  assertEquals("boundary",actual.get("strategy"));assertEquals(350,actual.get("target_tokens"));
 }
 @Test void oldPartialIndexCannotSwitchStrategy()throws Exception{
  var jdbc=mock(JdbcTemplate.class);
  when(jdbc.queryForObject(anyString(),eq(String.class),eq("d"),eq("v"))).thenReturn("{\"strategy\":\"legacy\"}");
  var state=new AdaptiveDocumentRepository.State("d",1L,"v",100,ProcessingStrategy.STANDARD_RAG,true,20,1,"PARTIAL_READY",null);
  new ChunkConfigurationStore(jdbc,new ObjectMapper()).freeze(state,Map.of("strategy","boundary"));
  verify(jdbc).update(anyString(),eq("d"),eq("v"),argThat((String value)->value.contains("\"strategy\":\"legacy\"")&&value.contains("\"hierarchy_enabled\":false")));
 }
}
