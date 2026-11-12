package com.example.server.knowledge;

/** Enterprise implementations may add department/project policy; tenant isolation is a separate boundary. */
public interface PermissionChecker {
    enum Operation { READ, WRITE }
    record Resource(String type,String id,Long ownerId,String sourceModule) {}
    boolean allowed(KnowledgeIdentity identity,Resource resource,Operation operation);
    default void require(KnowledgeIdentity identity,Resource resource,Operation operation){
        if(!allowed(identity,resource,operation))throw new SecurityException("无权访问该知识资源");
    }
}
