package com.example.server.document.adaptive;

import com.example.server.document.DocumentRecord;
import com.fasterxml.jackson.databind.*;
import org.springframework.stereotype.Component;
import java.text.Normalizer;
import java.util.*;
import java.util.regex.Pattern;

/** Whole-query allowlist. Facts mentioned inside document prose are never treated as storage metadata. */
@Component
public class StructuredDocumentQuery {
    private final RetrievalRoutingProperties properties;private final ObjectMapper mapper;
    private static final Map<String,Pattern> QUERIES=Map.of(
        "COUNT",Pattern.compile("(?:这些|当前|选中)?(?:文件数量|文档数量|有多少(?:个|份)?(?:文件|文档))|(?:how many (?:files|documents)|file count)"),
        "SIZE",Pattern.compile("(?:(?:这些|当前|选中)?(?:文件|文档)(?:的)?(?:大小|有多大)(?:是多少)?|(?:what is (?:the )?)?(?:file|document) size)"),
        "UPLOAD",Pattern.compile("(?:(?:这些|当前|选中)?(?:文件|文档)(?:的)?上传时间(?:是什么)?|(?:what is (?:the )?)?upload time)"),
        "PARSE",Pattern.compile("(?:(?:这些|当前|选中)?(?:文件|文档)(?:的)?)?解析状态|parsing status"),
        "INDEX",Pattern.compile("(?:(?:这些|当前|选中)?(?:文件|文档)(?:的)?)?索引状态|index status"));
    public StructuredDocumentQuery(RetrievalRoutingProperties properties,ObjectMapper mapper){this.properties=properties;this.mapper=mapper;}
    private String key(String q){String text=Normalizer.normalize(q,Normalizer.Form.NFKC).toLowerCase(Locale.ROOT).strip().replaceFirst("[?？。]$","").strip();
        return QUERIES.entrySet().stream().filter(e->e.getValue().matcher(text).matches()).map(Map.Entry::getKey).findFirst().orElse(null);}
    public boolean supported(String q){return properties.routed()&&properties.getRules().getOrDefault("metadata",true)&&key(q)!=null;}
    public JsonNode answer(String q,List<DocumentRecord> docs,List<AdaptiveDocumentRepository.State> states){
        if(!supported(q))return null;String field=key(q);var lines=new ArrayList<String>();
        if(field.equals("COUNT"))lines.add("选中文档数量："+docs.size());
        else for(int i=0;i<docs.size();i++){var doc=docs.get(i);var state=states.get(i);
            String value=switch(field){case "SIZE"->doc.sizeBytes()+" bytes";case "UPLOAD"->String.valueOf(doc.createdAt());case "PARSE"->doc.status()+" / "+doc.parsedPages()+" / "+doc.totalPages()+" 页";case "INDEX"->state==null?"NOT_INITIALIZED":state.state();default->throw new IllegalStateException();};
            lines.add(doc.filename()+"："+value);
        }
        var out=mapper.createObjectNode().put("answer",String.join("\n",lines)).put("insufficientEvidence",false)
            .put("retrievalMode","STRUCTURED").put("ruleVersion","retrieval-router-v2").put("coverage","METADATA").put("coverageBasis","STRUCTURED_METADATA").put("llmCalls",0);
        out.putArray("citations");var sources=out.putArray("source_documents");docs.forEach(d->sources.addObject().put("documentId",d.id()).put("filename",d.filename()));return out;
    }
}
