package com.example.server.document;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;

@Component
public class DoclingClient {
    private static final int MAX_RESPONSE_BYTES = 64 * 1024 * 1024;
    private static final int MAX_RESUME_STATE_BYTES = 256 * 1024;
    private final ObjectMapper mapper;
    private final String url;
    private final OkHttpClient client;
    public DoclingClient(ObjectMapper mapper, @Value("${document.docling.url:http://127.0.0.1:5001}") String url,
                         @Value("${document.docling.timeout-seconds:1200}") long timeout) {
        this.mapper = mapper;
        this.url = url.replaceAll("/+$", "");
        this.client = new OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS)
                .readTimeout(timeout, TimeUnit.SECONDS).callTimeout(timeout, TimeUnit.SECONDS).build();
    }
    public JsonNode parse(Path file, String filename) throws Exception {
        RequestBody body = new MultipartBody.Builder().setType(MultipartBody.FORM)
                .addFormDataPart("file", filename, RequestBody.create(file.toFile(), MediaType.parse("application/octet-stream"))).build();
        Request request = new Request.Builder().url(url + "/parse").post(body).build();
        try (Response response = client.newCall(request).execute()) {
            if (response.code() == 422 || response.code() == 413)
                throw new IllegalArgumentException("文档无法解析：请确认未加密、未损坏，且为 DOCX 或文本型 PDF，页数未超限");
            if (!response.isSuccessful() || response.body() == null) throw new IllegalStateException("Docling 解析服务暂不可用");
            if (response.body().contentLength() > MAX_RESPONSE_BYTES) throw new IllegalArgumentException("解析结果超过容量限制");
            byte[] bytes = response.body().byteStream().readNBytes(MAX_RESPONSE_BYTES + 1);
            if (bytes.length > MAX_RESPONSE_BYTES) throw new IllegalArgumentException("解析结果超过容量限制");
            JsonNode result = mapper.readTree(bytes);
            if (!result.path("blocks").isArray() || result.path("blocks").isEmpty())
                throw new IllegalArgumentException("未提取到有效正文，第一版暂不支持纯扫描 PDF");
            if (result.path("partial").asBoolean()) throw new IllegalArgumentException("文档仅完成部分解析，未生成完整总结");
            return result;
        } catch (java.net.ConnectException e) { throw new IllegalStateException("无法连接 Docling，请启动文档解析服务", e); }
    }
    @FunctionalInterface
    public interface EventConsumer { void accept(JsonNode event) throws Exception; }

    public void stream(Path file, String filename, int pages, int batches, String heading, EventConsumer consumer) throws Exception {
        stream(file, filename, pages, batches, heading, "DOCLING", consumer);
    }
    public void stream(Path file, String filename, int pages, int batches, String heading, String mode, EventConsumer consumer) throws Exception {
        stream(file, filename, pages, batches, heading, mode, null, consumer);
    }
    public void stream(Path file, String filename, int pages, int batches, String heading, String mode,
                       String resumeState, EventConsumer consumer) throws Exception {
        if (resumeState != null && resumeState.getBytes(StandardCharsets.UTF_8).length > MAX_RESUME_STATE_BYTES)
            throw new IllegalArgumentException("Parser resume state exceeds 256 KiB");
        MultipartBody.Builder body = new MultipartBody.Builder().setType(MultipartBody.FORM)
                .addFormDataPart("resume_pages", Integer.toString(pages))
                .addFormDataPart("resume_batches", Integer.toString(batches))
                .addFormDataPart("resume_heading", heading == null ? "" : heading)
                .addFormDataPart("parse_mode", mode)
                .addFormDataPart("file", filename, RequestBody.create(file.toFile(), MediaType.parse("application/octet-stream")));
        if (resumeState != null) body.addFormDataPart("resume_state", resumeState);
        var streaming = client.newBuilder().readTimeout(60, TimeUnit.SECONDS).callTimeout(0, TimeUnit.SECONDS).build();
        try (Response response = streaming.newCall(new Request.Builder().url(url + "/parse-stream").post(body.build()).build()).execute()) {
            if (response.code() == 413 || response.code() == 422) throw new IllegalArgumentException("文档格式、大小或解析参数不符合要求");
            if (!response.isSuccessful() || response.body() == null) throw new IllegalStateException("Docling 解析服务暂不可用");
            var source = response.body().source();
            long received = 0;
            while (!source.exhausted()) {
                String line = source.readUtf8LineStrict(8 * 1024 * 1024);
                received += line.length();
                if (received > MAX_RESPONSE_BYTES) throw new IllegalArgumentException("解析结果超过容量限制");
                JsonNode event = mapper.readTree(line);
                String type = event.path("type").asText();
                if ("error".equals(type)) {
                    if (!event.path("retryable").asBoolean(true)) throw new IllegalArgumentException(event.path("message").asText("文档无法解析"));
                    throw new IllegalStateException(event.path("message").asText("Docling 批次解析失败"));
                }
                consumer.accept(event);
                if ("complete".equals(type)) return;
            }
            throw new IllegalStateException("Docling 解析流提前结束");
        }
    }
}
