package com.example.server.knowledge;

/** Constructed by the server from an authenticated session, never deserialized from request JSON. */
public record KnowledgeIdentity(Long userId,java.util.Map<String,String> attributes) {
    public KnowledgeIdentity(Long userId){this(userId,java.util.Map.of());}
    public KnowledgeIdentity {
        if(userId==null||userId<1)throw new SecurityException("未认证身份");
        attributes=java.util.Map.copyOf(attributes);
    }
}
