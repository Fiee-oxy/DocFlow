package com.example.server.service;

import dev.langchain4j.exception.HttpException;
import org.junit.jupiter.api.Test;
import java.net.ConnectException;
import java.net.SocketTimeoutException;
import java.util.ArrayList;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.TimeoutException;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class ModelFailureTest {
    @Test void unwrapsTimeoutNetworkAndExecutorFailuresWithoutCopyingSensitiveMessages() {
        assertEquals(ModelFailure.Type.TIMEOUT, ModelFailure.classify(new IllegalStateException("wrapped", new TimeoutException())).type());
        assertEquals(ModelFailure.Type.TIMEOUT, ModelFailure.classify(new SocketTimeoutException()).type());
        assertEquals(ModelFailure.Type.NETWORK, ModelFailure.classify(new ConnectException()).type());
        assertEquals(ModelFailure.Type.EXECUTOR_BUSY, ModelFailure.classify(new RejectedExecutionException()).type());
        assertFalse(ModelFailure.classify(new RuntimeException("api-key=private; provider body=private")).getMessage().contains("private"));
    }
    @Test void preservesHttpStatusAndSeparatesAuthenticationRateLimitAndServerErrors() {
        for (int status : new int[]{401, 403, 429, 500, 400, 504}) {
            var http = mock(HttpException.class); when(http.statusCode()).thenReturn(status);
            var failure = ModelFailure.classify(new IllegalArgumentException("wrapper", http));
            var expected = switch(status) {
                case 401, 403 -> ModelFailure.Type.API_AUTH;
                case 429 -> ModelFailure.Type.API_RATE_LIMIT;
                case 500 -> ModelFailure.Type.API_SERVER;
                case 504 -> ModelFailure.Type.TIMEOUT;
                default -> ModelFailure.Type.API_REQUEST;
            };
            assertEquals(expected, failure.type()); assertEquals(status, failure.httpStatus());
        }
    }
    @Test void typedValidationFailuresSurviveWrappingAndObserverIsRemovedAfterScope() {
        var events = new ArrayList<ModelFailure>();
        var quote = new ModelFailure(ModelFailure.Type.CITATION_QUOTE, "VALIDATION");
        assertSame(quote, ModelFailure.classify(new IllegalStateException("wrapper", quote)));
        try (var scope = ModelFailure.observe(events::add)) { ModelFailure.record(quote); }
        ModelFailure.record(quote);
        assertEquals(1, events.size());
        assertEquals(ModelFailure.Type.JSON_FORMAT, ModelFailure.json(new RuntimeException()).type());
    }
}
