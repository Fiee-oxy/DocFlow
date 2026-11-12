package com.example.server.document.adaptive;

import com.example.server.document.*;
import com.fasterxml.jackson.databind.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

@Repository
public class AdaptiveDocumentRepository {
    private final JdbcTemplate jdbc;
    private final DocumentRepository documents;
    private final ObjectMapper mapper;
    private final TokenBudgetManager tokens;
    private final DocumentProcessingRouter router;
    public AdaptiveDocumentRepository(JdbcTemplate jdbc, DocumentRepository documents, ObjectMapper mapper,
            TokenBudgetManager tokens, DocumentProcessingRouter router) {
        this.jdbc=jdbc; this.documents=documents; this.mapper=mapper; this.tokens=tokens; this.router=router;
    }
    public static String hash(String text) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8))); }
        catch (Exception e) { throw new IllegalStateException(e); }
    }
    public record State(String documentId, Long ownerId, String version, long tokenCount, ProcessingStrategy strategy,
            boolean requested, int indexedChunks, int indexedBatches, String state, String error) {
        public boolean ready() { return "READY".equals(state); }
        public boolean partial() { return indexedChunks > 0; }
    }
    public State state(String id) {
        return jdbc.query("SELECT * FROM document_adaptive_state WHERE document_id=?", (r,n)->new State(id,
                r.getLong("owner_id"),r.getString("index_version"),r.getLong("token_count"),
                ProcessingStrategy.valueOf(r.getString("processing_strategy")),r.getBoolean("index_requested"),
                r.getInt("indexed_blocks"),r.getInt("indexed_batches"),r.getString("state"),r.getString("error_message")), id)
                .stream().findFirst().orElse(null);
    }
    /** Boundary carry is committed in the same transaction as the page cursor and batch payload. */
    public String resumeState(String id, int batch) {
        if (batch < 0) return null;
        var rows = jdbc.queryForList("SELECT COALESCE(NULLIF(JSON_EXTRACT(payload,'$.docxResumeState'),CAST('null' AS JSON)),JSON_EXTRACT(payload,'$.resumeState')) FROM document_parse_batches WHERE document_id=? AND batch_index=?",
                String.class, id, batch);
        return rows.isEmpty() || rows.getFirst() == null || "null".equals(rows.getFirst()) ? null : rows.getFirst();
    }
    /** Save parser units unchanged. This is source persistence, not RAG chunking. */
    public List<DocumentRepository.Block> sourceUnits(JsonNode event, int first, String prefix) {
        var result = new ArrayList<DocumentRepository.Block>();
        for (var item : event.path("blocks")) {
            if (item.path("text").asText().isBlank()) continue;
            String title=item.path("section").asText("正文");
            if(title.length()>2048)throw new IllegalArgumentException("章节标题路径超过2048字符，请简化文档标题层级");
            var source = mapper.createObjectNode(); source.put("id",prefix+"s"+result.size());
            source.put("ref",prefix+item.path("ref").asText()); source.put("text",item.path("text").asText());
            source.put("label",item.path("label").asText("text")); source.put("offset",0);
            source.put("title_path",title);
            source.set("attributes",item.path("attributes"));source.set("bbox",item.path("bbox"));
            if(item.has("body_node"))source.set("body_node",item.path("body_node"));
            if(item.has("node_ref"))source.set("node_ref",item.path("node_ref"));
            for(String field:List.of("source_part","part_kind","content_kind","images","fields","formulas","notes","nested_tables","revision_view","table_caption","docx_boundary_merge"))
                if(item.has(field))source.set(field,item.path(field));
            if(event.has("parseUnit"))source.put("parse_unit",event.path("parseUnit").asText());
            if(item.has("source_fragments"))source.set("fragments",item.path("source_fragments"));
            if(item.has("continuity"))source.set("continuity",item.path("continuity"));
            if(event.has("documentIR")){source.put("irSchemaVersion",event.path("documentIR").path("schemaVersion").asText());source.set("parser",event.path("documentIR").path("parser"));}
            source.set("pages",item.path("pages").isArray()?item.path("pages"):mapper.createArrayNode());
            result.add(new DocumentRepository.Block(first+result.size(), title.substring(0,Math.min(512,title.length())),
                    mapper.createArrayNode().add(source).toString(),item.path("text").asText(),null));
        }
        return result;
    }
    @Transactional
    public void commitBatch(String id, int batch, List<DocumentRepository.Block> blocks, int end, int total,
            String warnings, JsonNode event) {
        documents.appendBatch(id,batch,blocks,end,total,warnings);
        var doc=documents.get(id);
        ensure(id,doc.userId());
        jdbc.update("INSERT INTO document_parse_batches VALUES(?,?,?)",id,batch,event.toString());
        long added=blocks.stream().mapToLong(b->tokens.count(b.text())).sum();
        jdbc.update("UPDATE document_adaptive_state SET token_count=token_count+? WHERE document_id=?",added,id);
        refreshStrategy(id,false);
        saveSections(id,blocks);
    }
    public void ensure(String id, Long owner) {
        jdbc.update("INSERT IGNORE INTO document_adaptive_state(document_id,owner_id,index_version,counting_mode,processing_strategy) VALUES(?,?,?,?,?)",
                id,owner,router.indexVersion(),tokens.countingMode(),ProcessingStrategy.DIRECT.name());
    }
    public void finish(String id) { refreshStrategy(id,true); }
    private void refreshStrategy(String id, boolean complete) {
        var state=state(id); var strategy=router.route(state.tokenCount());
        boolean rag=strategy==ProcessingStrategy.STANDARD_RAG||strategy==ProcessingStrategy.LARGE_DOCUMENT_RAG;
        String status=rag||state.requested()? state.partial()?"PARTIAL_READY":"INDEXING"
                :complete?strategy==ProcessingStrategy.DIRECT?"DIRECT_READY":"HYBRID_READY":"PARSING";
        jdbc.update("UPDATE document_adaptive_state SET processing_strategy=?,index_requested=index_requested OR ?,state=? WHERE document_id=?",
                strategy.name(),rag,status,id);
    }
    /** Existing parsed records are imported from their saved sources, never re-parsed. */
    @Transactional
    public State bootstrap(String id, Long owner) throws Exception {
        if (state(id)!=null) return state(id);
        var doc=documents.owned(id,owner); ensure(id,owner);
        var blocks=documents.blocks(id); var event=mapper.createObjectNode(); var units=event.putArray("blocks");
        for (var block:blocks) for (var source:mapper.readTree(block.sourceJson())) {
            var item=units.addObject(); item.put("text",source.path("text").asText()); item.put("section",block.section());
            item.put("ref",source.path("ref").asText()); item.set("pages",source.path("pages")); item.put("label",source.path("label").asText("text"));
            var legacy=java.util.regex.Pattern.compile("^b(\\d+):").matcher(source.path("id").asText());
            item.put("legacyBatch",legacy.find()?Integer.parseInt(legacy.group(1)):0);
        }
        int batch=0;var payload=mapper.createObjectNode();var batchUnits=payload.putArray("blocks");long budget=0;
        if(!doc.parseComplete()&&doc.parseBatches()>0){
            for(int index=0;index<doc.parseBatches();index++){
                var original=mapper.createObjectNode();var originalUnits=original.putArray("blocks");
                for(var unit:units)if(unit.path("legacyBatch").asInt()==index)originalUnits.add(unit);
                jdbc.update("INSERT IGNORE INTO document_parse_batches VALUES(?,?,?)",id,index,original.toString());
            }
        }else for(var unit:units){
            long size=tokens.count(unit.path("text").asText());
            if(!batchUnits.isEmpty()&&(batchUnits.size()>=router.indexBatchSize()||budget+size>30000)){
                jdbc.update("INSERT IGNORE INTO document_parse_batches VALUES(?,?,?)",id,batch++,payload.toString());
                payload=mapper.createObjectNode();batchUnits=payload.putArray("blocks");budget=0;
            }
            batchUnits.add(unit);budget+=size;
        }
        if(!batchUnits.isEmpty())jdbc.update("INSERT IGNORE INTO document_parse_batches VALUES(?,?,?)",id,batch,payload.toString());
        long count=blocks.stream().mapToLong(b->tokens.count(b.text())).sum();
        jdbc.update("UPDATE document_adaptive_state SET token_count=? WHERE document_id=?",count,id);
        saveSections(id,blocks); refreshStrategy(id,doc.parseComplete()); return state(id);
    }
    private void saveSections(String id,List<DocumentRepository.Block> blocks) {
        for (var block:blocks) {
            String sectionTitle=block.section();Integer start=null,end=null;
            try{var sources=mapper.readTree(block.sourceJson());if(!sources.isEmpty())sectionTitle=sources.get(0).path("title_path").asText(sectionTitle);
                for(var source:sources)for(var page:source.path("pages")){int p=page.asInt();start=start==null?p:Math.min(start,p);end=end==null?p:Math.max(end,p);}}
            catch(Exception e){throw new IllegalArgumentException("原文来源格式不合法",e);}
            String[] path=sectionTitle.split(" / "); String parent=null, full="";
            for (String title:path) {
                full=full.isEmpty()?title:full+" / "+title; String section=hash(full);
                jdbc.update("INSERT INTO document_sections(document_id,section_id,parent_id,title,title_path,section_order,start_page,end_page) VALUES(?,?,?,?,?,?,?,?) ON DUPLICATE KEY UPDATE start_page=LEAST(COALESCE(start_page,VALUES(start_page)),COALESCE(VALUES(start_page),start_page)),end_page=GREATEST(COALESCE(end_page,VALUES(end_page)),COALESCE(VALUES(end_page),end_page))",
                        id,section,parent,title.substring(0,Math.min(512,title.length())),full.substring(0,Math.min(2048,full.length())),block.index(),start,end);
                parent=section;
            }
        }
    }
    public void requestIndex(String id) {
        jdbc.update("UPDATE document_adaptive_state SET index_requested=TRUE,state=IF(indexed_blocks>0,'PARTIAL_READY','INDEXING'),error_message=NULL,index_attempts=0,next_retry_at=NULL WHERE document_id=? AND state NOT IN ('READY','INDEXING','PARTIAL_READY')",id);
    }
    public void rebuild(String id){
        String prefix=router.indexVersion();String version=prefix.substring(0,Math.min(54,prefix.length()))+"-"+UUID.randomUUID().toString().substring(0,8);
        jdbc.update("UPDATE document_adaptive_state SET index_version=?,indexed_blocks=0,indexed_batches=0,index_requested=TRUE,state='INDEXING',index_attempts=0,next_retry_at=NULL,error_message=NULL WHERE document_id=?",version,id);
    }
    public List<String> pending() {
        return jdbc.queryForList("SELECT document_id FROM document_adaptive_state WHERE index_requested=TRUE AND state IN ('INDEXING','PARTIAL_READY') AND (next_retry_at IS NULL OR next_retry_at<=CURRENT_TIMESTAMP(3)) ORDER BY updated_at LIMIT 8",String.class);
    }
    public List<DocumentRecord> unclassified(){
        return jdbc.queryForList("SELECT f.id FROM document_files f WHERE f.parse_complete=TRUE AND f.total_blocks>0 AND NOT EXISTS(SELECT 1 FROM document_adaptive_state a WHERE a.document_id=f.id) ORDER BY f.created_at LIMIT 2",String.class)
                .stream().map(documents::get).toList();
    }
    public JsonNode nextBatch(State state) throws Exception {
        var rows=jdbc.queryForList("SELECT payload FROM document_parse_batches WHERE document_id=? AND batch_index=?",String.class,state.documentId(),state.indexedBatches());
        return rows.isEmpty()?null:mapper.readTree(rows.getFirst());
    }
    public void indexFailure(String id) {
        jdbc.update("UPDATE document_adaptive_state SET state=IF(index_attempts>=2,'FAILED',IF(indexed_blocks>0,'PARTIAL_READY','INDEXING')),next_retry_at=TIMESTAMPADD(SECOND,LEAST(120,5*POW(2,index_attempts)),CURRENT_TIMESTAMP(3)),index_attempts=index_attempts+1,error_message='索引构建失败，已保留检查点；请检查检索服务配置' WHERE document_id=?",id);
    }
    @Transactional
    public void indexed(State previous, JsonNode payload) {
        JsonNode chunks=payload.isArray()?payload:payload.path("chunks");
        if(!chunks.isArray())throw new IllegalArgumentException("索引服务缺少内容块数组");
        var status=jdbc.queryForObject("SELECT status FROM document_files WHERE id=? FOR UPDATE",String.class,previous.documentId());
        if("CANCELLED".equals(status))throw new java.util.concurrent.CancellationException("Cancelled document cannot publish index batches");
        if(payload.has("structure"))saveStructure(previous,payload,chunks);
        for(var c:chunks) jdbc.update("""
            INSERT INTO document_rag_chunks(chunk_id,document_id,index_version,section_id,block_index,content,search_text,source_json,token_count,page_start,page_end)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON DUPLICATE KEY UPDATE content=VALUES(content)
            """,c.path("chunk_id").asText(),previous.documentId(),previous.version(),c.path("section_id").asText(),
                previous.indexedChunks()+c.path("order").asInt(),c.path("content").asText(),c.path("search_text").asText(),
                c.path("sources").toString(),c.path("token_count").asInt(),c.path("page_start").isNumber()?c.path("page_start").asInt():null,
                c.path("page_end").isNumber()?c.path("page_end").asInt():null);
        int changed=jdbc.update("UPDATE document_adaptive_state SET indexed_blocks=indexed_blocks+?,indexed_batches=indexed_batches+1,state='PARTIAL_READY',error_message=NULL,index_attempts=0,next_retry_at=NULL WHERE document_id=? AND indexed_batches=? AND index_version=?",
                chunks.size(),previous.documentId(),previous.indexedBatches(),previous.version());
        if(changed!=1) throw new IllegalStateException("索引检查点冲突");
    }
    private void saveStructure(State state,JsonNode payload,JsonNode chunks){
        var structure=payload.path("structure");
        if(!structure.path("schema_version").asText().equals("document-hierarchy-v1")||!structure.path("document_id").asText().equals(state.documentId())||!structure.path("parse_version").asText().equals(state.version())
                ||structure.path("batch_index").asInt(-1)!=state.indexedBatches())throw new IllegalArgumentException("文档结构范围或批次不匹配");
        var children=new HashMap<String,JsonNode>();chunks.forEach(c->children.put(c.path("chunk_id").asText(),c));
        var assigned=new HashSet<String>();
        for(var parent:payload.path("parents")){
            String pid=parent.path("parent_id").asText();
            if(!pid.matches("[0-9a-f]{64}")||!parent.path("child_ids").isArray()||parent.path("child_ids").isEmpty())throw new IllegalArgumentException("父块结构不合法");
            if(!pid.equals(parent.path("source").path("id").asText())||!parent.path("content").asText().equals(parent.path("source").path("text").asText())
                    ||!parent.path("child_ids").equals(parent.path("source").path("child_ids")))throw new IllegalArgumentException("父块正文或引用不一致");
            for(var childId:parent.path("child_ids")){
                var child=children.get(childId.asText());
                if(child==null||!assigned.add(childId.asText())||!pid.equals(child.path("parent_id").asText())
                        ||!parent.path("section_id").asText().equals(child.path("section_id").asText()))throw new IllegalArgumentException("父子关系不匹配");
                for(var source:child.path("sources"))if(!pid.equals(source.path("parent_id").asText()))throw new IllegalArgumentException("子块来源缺少父节点");
            }
            jdbc.update("INSERT INTO document_parent_chunks(parent_id,document_id,index_version,batch_index,section_id,content,source_json,child_ids,token_count) VALUES(?,?,?,?,?,?,?,?,?) ON DUPLICATE KEY UPDATE content=VALUES(content),source_json=VALUES(source_json)",
                pid,state.documentId(),state.version(),state.indexedBatches(),parent.path("section_id").asText(),parent.path("content").asText(),
                parent.path("source").toString(),parent.path("child_ids").toString(),parent.path("token_count").asInt());
        }
        if(assigned.size()!=children.size())throw new IllegalArgumentException("内容块缺少父节点");
        jdbc.update("INSERT INTO document_structure_batches(document_id,index_version,batch_index,structure_json) VALUES(?,?,?,?) ON DUPLICATE KEY UPDATE structure_json=VALUES(structure_json)",
            state.documentId(),state.version(),state.indexedBatches(),structure.toString());
    }
    public List<JsonNode> structureBatches(String id,String version,int after,int limit){
        return jdbc.query("SELECT structure_json FROM document_structure_batches WHERE document_id=? AND index_version=? AND batch_index>? ORDER BY batch_index LIMIT ?",
            (r,n)->parseJson(r.getString(1)),id,version,after,limit);
    }
    public List<JsonNode> parseBatches(String id,int after,int limit){
        return jdbc.query("SELECT batch_index,payload FROM document_parse_batches WHERE document_id=? AND batch_index>? ORDER BY batch_index LIMIT ?",
            (r,n)->(JsonNode)mapper.createObjectNode().put("batch_index",r.getInt(1)).set("payload",parseJson(r.getString(2))),id,after,limit);
    }
    public JsonNode parentForHit(JsonNode hit){
        var rows=jdbc.query("""
            SELECT p.source_json,p.child_ids FROM document_parent_chunks p JOIN document_rag_chunks c
             ON c.document_id=p.document_id AND c.index_version=p.index_version AND c.section_id=p.section_id
             AND JSON_UNQUOTE(JSON_EXTRACT(c.source_json,'$[0].parent_id')) COLLATE utf8mb4_unicode_ci=p.parent_id
            WHERE c.document_id=? AND c.index_version=? AND c.chunk_id=? LIMIT 1
            """,(r,n)->{
                var source=(com.fasterxml.jackson.databind.node.ObjectNode)parseJson(r.getString(1));
                source.set("child_ids",parseJson(r.getString(2)));return (JsonNode)source;
            },hit.path("doc_id").asText(),hit.path("index_version").asText(),hit.path("chunk_id").asText());
        if(rows.isEmpty())return null;var source=rows.getFirst();
        boolean member=false;for(var child:source.path("child_ids"))if(child.asText().equals(hit.path("chunk_id").asText()))member=true;
        if(!member)throw new IllegalStateException("已发布父块缺少子块成员");return source;
    }
    public Map<String,JsonNode> parentsForHits(List<JsonNode> hits){
        if(hits.isEmpty())return Map.of();if(hits.size()>100)throw new IllegalArgumentException("父块批量读取超过上限");
        var clauses=new ArrayList<String>();var args=new ArrayList<Object>();
        for(var hit:hits){clauses.add("(c.document_id=? AND c.index_version=? AND c.chunk_id=?)");
            args.add(hit.path("doc_id").asText());args.add(hit.path("index_version").asText());args.add(hit.path("chunk_id").asText());}
        String sql="""
            SELECT c.chunk_id,p.source_json,p.child_ids FROM document_parent_chunks p JOIN document_rag_chunks c
             ON c.document_id=p.document_id AND c.index_version=p.index_version AND c.section_id=p.section_id
             AND JSON_UNQUOTE(JSON_EXTRACT(c.source_json,'$[0].parent_id')) COLLATE utf8mb4_unicode_ci=p.parent_id
            WHERE
            """+String.join(" OR ",clauses);
        var result=new LinkedHashMap<String,JsonNode>();
        jdbc.query(sql,(org.springframework.jdbc.core.RowCallbackHandler)row->{
            String child=row.getString(1);var source=(com.fasterxml.jackson.databind.node.ObjectNode)parseJson(row.getString(2));source.set("child_ids",parseJson(row.getString(3)));
            boolean member=false;for(var id:source.path("child_ids"))if(child.equals(id.asText()))member=true;
            if(!member)throw new IllegalStateException("已发布父块缺少子块成员");result.put(child,source);
        },args.toArray());return result;
    }
    public JsonNode parentById(String id,String version,String parentId){
        var rows=jdbc.query("SELECT source_json FROM document_parent_chunks WHERE document_id=? AND index_version=? AND parent_id=?",
            (r,n)->parseJson(r.getString(1)),id,version,parentId);return rows.isEmpty()?null:rows.getFirst();
    }
    private JsonNode parseJson(String raw){try{return mapper.readTree(raw);}catch(Exception e){throw new IllegalStateException("文档结构数据损坏",e);}}
    public void publishReady(String id) {
        jdbc.update("UPDATE document_adaptive_state a JOIN document_files f ON a.document_id=f.id SET a.state='READY' WHERE a.document_id=? AND f.parse_complete=TRUE AND a.indexed_blocks>0 AND a.indexed_batches=(SELECT COUNT(*) FROM document_parse_batches b WHERE b.document_id=a.document_id)",id);
    }
    public List<JsonNode> sections(String id) {
        return jdbc.query("SELECT * FROM document_sections WHERE document_id=? ORDER BY section_order,title_path",(r,n)->{
            var o=mapper.createObjectNode();o.put("section_id",r.getString("section_id"));o.put("parent_id",r.getString("parent_id"));
            o.put("title",r.getString("title"));o.put("title_path",r.getString("title_path"));o.put("doc_id",id);o.put("section_order",r.getInt("section_order"));
            o.set("start_page",mapper.valueToTree(r.getObject("start_page")));o.set("end_page",mapper.valueToTree(r.getObject("end_page")));return (JsonNode)o;
        },id);
    }
    public List<JsonNode> sourceEvidence(String id) throws Exception {
        var out=new ArrayList<JsonNode>();var doc=documents.get(id);String version=state(id).version();
        for(var b:documents.blocks(id))for(var source:mapper.readTree(b.sourceJson())) {
            var s=source.deepCopy(); ((com.fasterxml.jackson.databind.node.ObjectNode)s).put("doc_id",id).put("filename",doc.filename())
                .put("section",source.path("title_path").asText(b.section())).put("section_id",hash(source.path("title_path").asText(b.section()))).put("blockIndex",b.index()).put("index_version",version)
                .put("chunk_id",hash(id+":"+version+":"+source.path("id").asText()));
            ((com.fasterxml.jackson.databind.node.ObjectNode)s).put("id",id+":"+source.path("id").asText());out.add(s);
        }
        return out;
    }
    public String cached(String key,Long owner) {
        return jdbc.queryForList("SELECT result_json FROM document_analysis_cache WHERE cache_key=? AND owner_id=?",String.class,key,owner).stream().findFirst().orElse(null);
    }
    public boolean publishedHit(JsonNode hit) {
        var rows=jdbc.queryForList("SELECT block_index FROM document_rag_chunks WHERE chunk_id=? AND document_id=? AND index_version=?",Integer.class,
                hit.path("chunk_id").asText(),hit.path("doc_id").asText(),hit.path("index_version").asText());
        if(rows.isEmpty())return false;((com.fasterxml.jackson.databind.node.ObjectNode)hit).put("block_index",rows.getFirst());return true;
    }
    public List<JsonNode> neighbors(JsonNode hit,String filename){
        int order=hit.path("block_index").asInt();String id=hit.path("doc_id").asText(),version=hit.path("index_version").asText();
        return jdbc.query("SELECT * FROM document_rag_chunks WHERE document_id=? AND index_version=? AND section_id=? AND block_index BETWEEN ? AND ? AND chunk_id<>? ORDER BY block_index LIMIT 2",(r,n)->{
            var source=mapper.createObjectNode();source.put("id",r.getString("chunk_id"));source.put("chunk_id",r.getString("chunk_id"));source.put("doc_id",id);
            source.put("filename",filename);source.put("index_version",version);source.put("text",r.getString("content"));source.put("blockIndex",r.getInt("block_index"));
            try{var refs=mapper.readTree(r.getString("source_json"));if(!refs.isEmpty()){source.set("pages",refs.get(0).path("pages"));source.put("ref",refs.get(0).path("ref").asText());source.put("section",refs.get(0).path("section").asText());}}
            catch(Exception e){throw new IllegalStateException("相邻内容块来源损坏",e);}return (JsonNode)source;
        },id,version,hit.path("section_id").asText(),Math.max(0,order-1),order+1,hit.path("chunk_id").asText());
    }
    public List<JsonNode> ragEvidence(String id,String version) {
        var doc=documents.get(id);
        return jdbc.query("SELECT * FROM document_rag_chunks WHERE document_id=? AND index_version=? ORDER BY block_index",(r,n)->{
            var o=mapper.createObjectNode();o.put("id",r.getString("chunk_id"));o.put("chunk_id",r.getString("chunk_id"));
            o.put("doc_id",id);o.put("filename",doc.filename());o.put("index_version",version);o.put("section_id",r.getString("section_id"));
            o.put("text",r.getString("content"));
            try{var sources=mapper.readTree(r.getString("source_json"));if(!sources.isEmpty()){
                o.set("pages",sources.get(0).path("pages"));o.put("ref",sources.get(0).path("ref").asText());o.put("section",sources.get(0).path("section").asText());}}
            catch(Exception e){throw new IllegalStateException("索引来源格式损坏",e);}return (JsonNode)o;
        },id,version);
    }
    public List<JsonNode> sectionEvidence(String id,String version,List<String> sections,int limit){
        return sectionEvidence(id,version,sections,-1,limit);
    }
    public List<JsonNode> sectionEvidence(String id,String version,List<String> sections,int after,int limit){
        if(sections.isEmpty())return List.of();
        var args=new ArrayList<Object>();args.add(id);args.add(version);args.addAll(sections);args.add(after);args.add(limit);
        String placeholders=String.join(",",Collections.nCopies(sections.size(),"?"));
        String filename=documents.get(id).filename();
        return jdbc.query("SELECT chunk_id,content,source_json,block_index,section_id FROM document_rag_chunks WHERE document_id=? AND index_version=? AND section_id IN ("+placeholders+") AND block_index>? ORDER BY block_index LIMIT ?",(r,n)->{
            String chunk=r.getString("chunk_id");var source=mapper.createObjectNode();source.put("id",chunk).put("chunk_id",chunk).put("doc_id",id)
                    .put("filename",filename).put("index_version",version).put("text",r.getString("content"))
                    .put("blockIndex",r.getInt("block_index")).put("section_id",r.getString("section_id"));
            try{var refs=mapper.readTree(r.getString("source_json"));if(!refs.isEmpty()){
                source.set("pages",refs.get(0).path("pages"));source.put("section",refs.get(0).path("section").asText());source.put("ref",refs.get(0).path("ref").asText());}}
            catch(Exception e){throw new IllegalStateException("章节来源损坏",e);}return (JsonNode)source;
        },args.toArray());
    }
    public void cache(String key,Long owner,JsonNode result) {
        jdbc.update("INSERT IGNORE INTO document_analysis_cache(cache_key,owner_id,result_json) VALUES(?,?,?)",key,owner,result.toString());
    }
    public JdbcTemplate jdbc() {return jdbc;}
}
