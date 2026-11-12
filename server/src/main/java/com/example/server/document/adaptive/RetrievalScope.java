package com.example.server.document.adaptive;
import java.util.List;
/** Built only after authorization. Owner is the project's current security boundary, not a fabricated tenant. */
public record RetrievalScope(Long ownerId,List<DocumentVersion> documents,List<String> sectionIds,List<Integer> pageRange){
    public record DocumentVersion(String documentId,String indexVersion){}
}
