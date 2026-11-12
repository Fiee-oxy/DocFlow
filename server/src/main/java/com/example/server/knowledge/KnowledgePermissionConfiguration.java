package com.example.server.knowledge;

import org.springframework.context.annotation.*;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;

@Configuration
public class KnowledgePermissionConfiguration {
    @Bean @ConditionalOnMissingBean(KnowledgeIdentityAdapter.class)
    public KnowledgeIdentityAdapter localKnowledgeIdentityAdapter(){return KnowledgeIdentity::new;}
    @Bean @ConditionalOnMissingBean(PermissionChecker.class)
    public PermissionChecker personalPermissionChecker(){
        return (identity,resource,operation)->identity.userId().equals(resource.ownerId());
    }
}
