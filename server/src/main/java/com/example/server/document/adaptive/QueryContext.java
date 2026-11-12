package com.example.server.document.adaptive;
import java.util.*;
public record QueryContext(String query,Long ownerId,List<String> documentIds,boolean documentGrounded,
    boolean generalChatMode,String conversationId,Map<String,Object> attributes,RetrievalScope scope){}
