package com.example.server.service;

import dev.langchain4j.exception.HttpException;
import java.io.IOException;
import java.net.SocketTimeoutException;
import java.net.http.HttpTimeoutException;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Consumer;

/** Stable diagnostics: never persist provider response bodies, prompts or credentials. */
public final class ModelFailure extends IllegalStateException {
    public enum Type {
        TIMEOUT("模型调用超时", true), API_AUTH("模型接口鉴权失败，请检查密钥与权限", false),
        API_RATE_LIMIT("模型接口限流", true), API_SERVER("模型服务暂时异常", true),
        API_REQUEST("模型接口拒绝请求，请检查请求配置", false), NETWORK("模型接口网络连接异常", true),
        JSON_FORMAT("模型返回内容不是合法 JSON 对象", true), OUTPUT_SCHEMA("模型返回字段或知识点结构不符合要求", true),
        CITATION_SOURCE("引用的来源 ID 不存在", true), CITATION_QUOTE("引用文字无法逐字匹配原文或长度不合法", true),
        EMPTY_RESPONSE("模型返回空内容", true), EXECUTOR_BUSY("模型调用线程池繁忙", true),
        INTERRUPTED("任务被中断", true), UNKNOWN("未分类的任务异常", true),
        LEGACY_UNKNOWN("旧版本有重试记录，但未保存具体失败原因", true);
        public final String message;
        public final boolean retryable;
        Type(String message, boolean retryable) { this.message = message; this.retryable = retryable; }
    }
    private final Type type;
    private final String phase;
    private final Integer httpStatus;
    private static final ThreadLocal<Consumer<ModelFailure>> OBSERVER = new ThreadLocal<>();
    public ModelFailure(Type type, String phase) { this(type, phase, null, null); }
    private ModelFailure(Type type, String phase, Integer status, Throwable cause) {
        super(type.message, cause); this.type = type; this.phase = phase; this.httpStatus = status;
    }
    public Type type() { return type; }
    public String phase() { return phase; }
    public Integer httpStatus() { return httpStatus; }
    public boolean retryable() { return type.retryable; }
    public static ModelFailure json(Throwable cause) { return new ModelFailure(Type.JSON_FORMAT, "JSON_PARSE", null, cause); }
    public static ModelFailure classify(Throwable error) {
        List<Throwable> chain = new ArrayList<>();
        Set<Throwable> seen = Collections.newSetFromMap(new IdentityHashMap<>());
        for (Throwable current = error; current != null && chain.size() < 20 && seen.add(current); current = current.getCause()) {
            if (current instanceof ModelFailure failure) return failure;
            chain.add(current);
        }
        for (var cause : chain) if (cause instanceof HttpException http) {
            int status = http.statusCode();
            Type type = status == 401 || status == 403 ? Type.API_AUTH : status == 429 ? Type.API_RATE_LIMIT
                    : status == 408 || status == 504 ? Type.TIMEOUT : status >= 500 ? Type.API_SERVER : Type.API_REQUEST;
            return new ModelFailure(type, "MODEL_CALL", status, error);
        }
        for (var cause : chain) if (cause instanceof TimeoutException || cause instanceof SocketTimeoutException
                || cause instanceof HttpTimeoutException || cause instanceof AgentExecutionBudget.DeadlineExceededException)
            return new ModelFailure(Type.TIMEOUT, "MODEL_CALL", null, error);
        for (var cause : chain) if (cause instanceof RejectedExecutionException)
            return new ModelFailure(Type.EXECUTOR_BUSY, "MODEL_CALL", null, error);
        for (var cause : chain) if (cause instanceof InterruptedException)
            return new ModelFailure(Type.INTERRUPTED, "WORKER", null, error);
        for (var cause : chain) if (cause instanceof IOException)
            return new ModelFailure(Type.NETWORK, "MODEL_CALL", null, error);
        return new ModelFailure(Type.UNKNOWN, "WORKER", null, error);
    }
    public interface Scope extends AutoCloseable { @Override void close(); }
    public static Scope observe(Consumer<ModelFailure> observer) {
        var previous = OBSERVER.get(); OBSERVER.set(observer);
        return () -> { if (previous == null) OBSERVER.remove(); else OBSERVER.set(previous); };
    }
    public static void record(ModelFailure failure) {
        var observer = OBSERVER.get();
        if (observer != null) observer.accept(failure);
    }
}
