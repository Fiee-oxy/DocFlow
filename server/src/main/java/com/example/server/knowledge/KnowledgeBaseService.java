package com.example.server.knowledge;

import com.example.server.document.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.util.*;

@Service
public class KnowledgeBaseService {
    private final JdbcTemplate jdbc;
    private final PermissionChecker permissions;
    private final DocumentRepository documents;
    private final DocumentUploadService uploads;
    private KnowledgeIdentityAdapter identities=KnowledgeIdentity::new;
    @org.springframework.beans.factory.annotation.Autowired
    public void identityAdapter(KnowledgeIdentityAdapter adapter){this.identities=adapter;}
    public KnowledgeBaseService(JdbcTemplate jdbc,PermissionChecker permissions,DocumentRepository documents,DocumentUploadService uploads){
        this.jdbc=jdbc;this.permissions=permissions;this.documents=documents;this.uploads=uploads;
    }
    public record Base(String id,Long ownerId,String name,String sourceModule,boolean isDefault){}
    public Base owned(String id,Long owner,PermissionChecker.Operation operation){
        var rows=jdbc.query("SELECT * FROM knowledge_bases WHERE id=?",(rs,n)->new Base(rs.getString("id"),rs.getLong("owner_id"),
                rs.getString("name"),rs.getString("source_module"),rs.getObject("default_slot")!=null),id);
        if(rows.isEmpty())throw new NoSuchElementException("知识库不存在");
        Base base=rows.getFirst();
        // Personal ownership remains a mandatory boundary until shared ACL/tenancy is implemented.
        if(!base.ownerId().equals(owner))throw new SecurityException("无权访问该知识库");
        permissions.require(identities.resolveAuthenticatedUser(owner),new PermissionChecker.Resource("KNOWLEDGE_BASE",id,base.ownerId(),base.sourceModule()),operation);
        return base;
    }
    public List<Base> list(Long owner){
        ensureDefault(jdbc,owner);
        return jdbc.query("SELECT * FROM knowledge_bases WHERE owner_id=? ORDER BY default_slot DESC,created_at,id",
                (rs,n)->new Base(rs.getString("id"),rs.getLong("owner_id"),rs.getString("name"),rs.getString("source_module"),rs.getObject("default_slot")!=null),owner)
                .stream().filter(b->permissions.allowed(identities.resolveAuthenticatedUser(owner),new PermissionChecker.Resource("KNOWLEDGE_BASE",b.id(),owner,b.sourceModule()),PermissionChecker.Operation.READ)).toList();
    }
    public Base create(Long owner,String name,String module){
        if(name==null||name.isBlank()||name.strip().length()>100)throw new IllegalArgumentException("知识库名称须为1～100字符");
        if(module==null||!module.matches("[A-Za-z0-9_-]{1,64}"))throw new IllegalArgumentException("业务分类须为1～64位字母、数字、下划线或短横线");
        String id=UUID.randomUUID().toString();
        permissions.require(identities.resolveAuthenticatedUser(owner),new PermissionChecker.Resource("KNOWLEDGE_BASE",id,owner,module),PermissionChecker.Operation.WRITE);
        jdbc.update("INSERT INTO knowledge_bases(id,owner_id,name,source_module) VALUES(?,?,?,?)",id,owner,name.strip(),module);
        return owned(id,owner,PermissionChecker.Operation.READ);
    }
    public static void ensureDefault(JdbcTemplate jdbc,Long owner){
        jdbc.update("INSERT IGNORE INTO knowledge_bases(id,owner_id,name,source_module,default_slot) VALUES(?,?,'我的知识库','personal',1)","personal-"+owner,owner);
    }
    private static final String FIELDS="""
            f.id,f.filename,f.size_bytes AS sizeBytes,f.status,f.stage,f.total_blocks AS totalBlocks,
            f.completed_blocks AS completedBlocks,f.error_message AS errorMessage,f.created_at AS createdAt,
            f.total_pages AS totalPages,f.parsed_pages AS parsedPages,f.parse_complete AS parseComplete,
            k.knowledge_base_id AS knowledgeBaseId,k.logical_document_id AS logicalDocumentId,k.version_number AS versionNumber
            """;
    public List<Map<String,Object>> documents(Long owner,String baseId){
        owned(baseId,owner,PermissionChecker.Operation.READ);
        return jdbc.queryForList("SELECT "+FIELDS+" FROM document_files f JOIN knowledge_documents k ON k.document_id=f.id WHERE k.knowledge_base_id=? AND k.owner_id=? ORDER BY f.created_at DESC,f.id",baseId,owner)
                .stream().filter(row->canRead(owner,row.get("id").toString())).toList();
    }
    private boolean canRead(Long owner,String id){try{documents.owned(id,owner);return true;}catch(SecurityException e){return false;}}
    public List<Map<String,Object>> versions(Long owner,String documentId){
        documents.owned(documentId,owner);
        String logical=jdbc.queryForObject("SELECT logical_document_id FROM knowledge_documents WHERE document_id=? AND owner_id=?",String.class,documentId,owner);
        return jdbc.queryForList("SELECT "+FIELDS+" FROM document_files f JOIN knowledge_documents k ON k.document_id=f.id WHERE k.logical_document_id=? AND k.owner_id=? ORDER BY k.version_number DESC",logical,owner)
                .stream().filter(row->canRead(owner,row.get("id").toString())).toList();
    }
    @Transactional
    public String initialize(Long owner,String baseId,String previous,String filename,long size){
        if(baseId==null||baseId.isBlank())baseId="personal-"+owner;
        ensureDefault(jdbc,owner);owned(baseId,owner,PermissionChecker.Operation.WRITE);
        String logical=null;int version=1;
        if(previous!=null&&!previous.isBlank()){
            documents.writable(previous,owner);
            var membership=jdbc.queryForMap("SELECT * FROM knowledge_documents WHERE document_id=? AND owner_id=?",previous,owner);
            if(!baseId.equals(membership.get("knowledge_base_id")))throw new IllegalArgumentException("新版本必须属于原知识库");
            logical=membership.get("logical_document_id").toString();
            // Lock the common root, so concurrent uploads cannot allocate the same revision number.
            jdbc.queryForMap("SELECT document_id FROM knowledge_documents WHERE document_id=? FOR UPDATE",logical);
            version=jdbc.queryForObject("SELECT version_number FROM knowledge_documents WHERE logical_document_id=? ORDER BY version_number DESC LIMIT 1 FOR UPDATE",Integer.class,logical)+1;
        }
        String id=uploads.initialize(filename,size,owner);
        jdbc.update("UPDATE knowledge_documents SET knowledge_base_id=?,logical_document_id=?,version_number=? WHERE document_id=?",baseId,logical==null?id:logical,version,id);
        return id;
    }
    private boolean fullyUsable(Map<String,Object> row){
        boolean complete=Boolean.TRUE.equals(row.get("parseComplete")) || row.get("parseComplete") instanceof Number n && n.intValue()!=0;
        String strategy=String.valueOf(row.get("processingStrategy"));
        return complete && ("READY".equals(row.get("indexState")) || "DIRECT".equals(strategy) || "HYBRID_DECISION".equals(strategy));
    }
    public List<String> resolveScope(Long owner,List<String> baseIds,List<String> documentIds){
        var bases=baseIds==null?List.<String>of():baseIds.stream().distinct().toList();
        var explicit=documentIds==null?List.<String>of():documentIds.stream().distinct().toList();
        if(bases.size()>16||explicit.size()>64)throw new IllegalArgumentException("查询范围过大，请缩小范围");
        for(String base:bases)owned(base,owner,PermissionChecker.Operation.READ);
        if(!explicit.isEmpty()){
            for(String id:explicit){documents.owned(id,owner);
                String base=jdbc.queryForObject("SELECT knowledge_base_id FROM knowledge_documents WHERE document_id=? AND owner_id=?",String.class,id,owner);
                owned(base,owner,PermissionChecker.Operation.READ);
                if(!bases.isEmpty()&&!bases.contains(base))throw new IllegalArgumentException("所选文档不属于所选知识库");
            }
            return explicit;
        }
        if(bases.isEmpty())throw new IllegalArgumentException("请选择知识库或文档");
        var ids=new ArrayList<String>();
        for(String base:bases){
            var candidates=jdbc.queryForList("""
                    SELECT k.document_id AS documentId,k.logical_document_id AS logicalId,
                      f.parse_complete AS parseComplete,a.processing_strategy AS processingStrategy,
                      a.state AS indexState,COALESCE(a.indexed_blocks,0) AS indexedBlocks
                    FROM knowledge_documents k JOIN document_files f ON f.id=k.document_id
                    LEFT JOIN document_adaptive_state a ON a.document_id=f.id
                    WHERE k.knowledge_base_id=? AND k.owner_id=? AND f.total_blocks>0
                    ORDER BY k.logical_document_id,k.version_number DESC
                    """,base,owner);
            var groups=new LinkedHashMap<String,List<Map<String,Object>>>();
            for(var row:candidates)if(canRead(owner,row.get("documentId").toString()))
                groups.computeIfAbsent(row.get("logicalId").toString(),key->new ArrayList<>()).add(row);
            for(var versions:groups.values()){
                var selected=versions.stream().filter(this::fullyUsable).findFirst()
                        .orElseGet(()->versions.stream().filter(row->((Number)row.get("indexedBlocks")).intValue()>0)
                                .findFirst().orElse(versions.getFirst()));
                ids.add(selected.get("documentId").toString());
            }
        }
        if(ids.isEmpty())throw new IllegalArgumentException("所选知识库尚无可检索内容");
        return ids;
    }
}
