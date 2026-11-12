package com.example.server.document.adaptive;
import com.example.server.document.*;
import com.example.server.utils.DeepSeekUtils;
import com.fasterxml.jackson.databind.*;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import java.nio.file.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.*;
import static org.mockito.Mockito.*;
/** Explicit opt-in read-only preview against the selected local document; no production writes. */
class LiveSectionPreviewTest {
    @Test void realPreviewAndReadCursorCoverTheSelectedGraduationChapter()throws Exception{
        String fixture=System.getenv("DOCUMENT_EVAL_FIXTURE");assumeTrue(fixture!=null);
        String casesFile=System.getenv("DOCUMENT_CREDIT_CASES");assumeTrue(casesFile!=null,"External credit cases required for this opt-in evaluation");
        var mapper=new ObjectMapper();var scope=mapper.readTree(Files.readString(Path.of(fixture)));
        var env=new HashMap<String,String>();for(var line:Files.readAllLines(Path.of("../.env"))){int eq=line.indexOf('=');
            if(eq>0){String value=line.substring(eq+1).strip();if(value.length()>1&&(value.startsWith("\"")&&value.endsWith("\"")||value.startsWith("'")&&value.endsWith("'")))value=value.substring(1,value.length()-1);env.put(line.substring(0,eq),value);}}
        var ds=new DriverManagerDataSource(env.getOrDefault("DB_URL","jdbc:mysql://127.0.0.1:3307/media_db?allowPublicKeyRetrieval=true&useSSL=false"),env.get("DB_USERNAME"),env.get("DB_PASSWORD"));
        var jdbc=new JdbcTemplate(ds);var documents=new DocumentRepository(jdbc);
        var settings=new AdaptiveSettings(8000,20000,50000,32768,2048,1024,2000,4500,8000,12000,20,20,20,8,60,8,64,32,"http://127.0.0.1:5002","","eval");
        var tokens=new TokenBudgetManager(settings);var repository=new AdaptiveDocumentRepository(jdbc,documents,mapper,tokens,new DocumentProcessingRouter(settings));
        var tasks=mock(AdaptiveQueryTaskStore.class);var model=mock(DeepSeekUtils.class);var locks=mock(org.redisson.api.RedissonClient.class);
        var service=new AdaptiveQueryService(documents,repository,tasks,settings,tokens,new QueryStrategyRouter(),new LocalRetrievalClient(settings,mapper),new AdaptiveContextBuilder(tokens,mapper),model,mapper,locks,Runnable::run,Runnable::run);
        String doc=scope.path("documentId").asText();long owner=scope.path("ownerId").asLong();
        var preview=service.preview(owner,List.of(doc),"大数据专业毕业要求",50);
        Files.writeString(Path.of("target/live-graduation-preview.json"),mapper.writerWithDefaultPrettyPrinter().writeValueAsString(preview));
        assertTrue(preview.path("ready").asBoolean());assertTrue(preview.path("retrieval").get(0).path("sectionConstrained").asBoolean());
        var ids=new ArrayList<String>();preview.path("retrieval").get(0).path("sectionIds").forEach(s->ids.add(s.asText()));
        var seen=new HashSet<String>();int after=-1;boolean more=true;int pages=0;
        while(more&&pages++<100){var page=service.readSection(owner,doc,scope.path("version").asText(),ids,after,3000);
            for(var source:page.path("sources"))assertTrue(seen.add(source.path("id").asText()),"Cursor duplicated a block");
            more=page.path("hasMore").asBoolean();int next=page.path("nextAfterBlockIndex").asInt();if(more)assertTrue(next>after);after=next;}
        assertFalse(more);assertEquals(preview.path("retrieval").get(0).path("sectionSources").asInt(),seen.size());
        String text=preview.path("evidence").toString();for(int number=1;number<=12;number++)assertTrue(text.matches("(?s).*\\b"+number+"\\s*、.*"),"Missing requirement "+number);
        verifyNoInteractions(model,tasks,locks);
        // Regression: academic skills requirements and numeric credit requirements are sibling sections.
        var creditCases=mapper.readTree(Files.readString(Path.of(casesFile)));int creditCase=0;
        var creditReport=mapper.createArrayNode();
        for(var creditCaseData:creditCases){String question=creditCaseData.path("question").asText();
            var credit=service.preview(owner,List.of(doc),question,50);
            Files.writeString(Path.of(creditCase++==0?"target/live-credit-preview.json":"target/live-credit-preview-"+creditCase+".json"),mapper.writerWithDefaultPrettyPrinter().writeValueAsString(credit));
            assertEquals("SUBJECT_FACT",credit.path("retrieval").get(0).path("scopeType").asText());
            assertFalse(credit.path("retrieval").get(0).has("sectionExpansion"));
            assertTrue(credit.path("retrieval").get(0).path("topK").get(0).path("content").asText()
                    .matches("(?s).*最低完成\\s*"+creditCaseData.path("minimumCredits").asInt()+"\\s*学分.*"),
                    "Semester or individual-course credit overtook the graduation minimum: "+question);
            assertTrue(java.util.stream.StreamSupport.stream(credit.path("evidence").spliterator(),false).anyMatch(s->
                    s.path("text").asText().matches("(?s).*最低完成\\s*"+creditCaseData.path("minimumCredits").asInt()+"\\s*学分.*")
                            &&s.path("section").asText().contains(creditCaseData.path("chapter").asText())
                            &&s.path("section").asText().contains(creditCaseData.path("subject").asText())
                            &&java.util.stream.StreamSupport.stream(s.path("pages").spliterator(),false).anyMatch(p->p.asInt()==creditCaseData.path("physicalPage").asInt())),
                    "Numeric credit source was omitted: "+question);
            creditReport.addObject().put("question",question).put("sourceSelected",true).put("llmCalls",0)
                    .put("scopeType",credit.path("retrieval").get(0).path("scopeType").asText());
        }
        Files.writeString(Path.of("target/credit-facts-evaluation.json"),mapper.writerWithDefaultPrettyPrinter().writeValueAsString(creditReport));
        verifyNoInteractions(model,tasks,locks);
    }
}
