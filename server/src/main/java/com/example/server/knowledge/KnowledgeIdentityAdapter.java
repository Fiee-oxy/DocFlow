package com.example.server.knowledge;

/** Map an already authenticated local account to trusted enterprise attributes on the server. */
public interface KnowledgeIdentityAdapter {
    KnowledgeIdentity resolveAuthenticatedUser(Long localUserId);
}
