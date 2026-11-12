package com.example.server.document;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.nio.file.Files;
import static org.junit.jupiter.api.Assertions.*;

class DoclingClientTest {
    @Test void forwardsBoundaryCarryWithoutChangingItsJson(@TempDir Path dir) throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        var request = new java.util.concurrent.atomic.AtomicReference<String>();
        server.createContext("/parse-stream", exchange -> {
            request.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            byte[] bytes = "{\"type\":\"complete\",\"totalPages\":2}\n".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes); exchange.close();
        });
        server.start();
        try {
            var client = new DoclingClient(new ObjectMapper(), "http://127.0.0.1:" + server.getAddress().getPort(), 5);
            Path file = Files.writeString(dir.resolve("book.pdf"), "%PDF-test");
            String state = "{\"version\":1,\"pending\":{\"text\":\"跨页正文\",\"pages\":[1]}}";
            client.stream(file, "book.pdf", 1, 1, "Chapter", "FAST", state, event -> {});
            assertTrue(request.get().contains("name=\"resume_state\""));
            assertTrue(request.get().contains(state));
            String pagePart = request.get().substring(request.get().indexOf("name=\"resume_pages\""));
            assertTrue(pagePart.substring(0, pagePart.indexOf("\r\n--")).endsWith("\r\n\r\n1"));
            client.stream(file, "book.pdf", 1, 1, "Chapter", "FAST", event -> {});
            assertFalse(request.get().contains("name=\"resume_state\""));
            // The cap is UTF-8 bytes, not Java character count.
            assertThrows(IllegalArgumentException.class, () -> client.stream(file, "book.pdf", 1, 1, "", "FAST",
                    "文".repeat(90_000), event -> {}));
        } finally { server.stop(0); }
    }
    @Test void streamsBatchesAndRejectsMissingCompletion(@TempDir Path dir) throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        String[] payload = {"{\"type\":\"metadata\",\"totalPages\":2}\n{\"type\":\"batch\",\"endPage\":2}\n{\"type\":\"complete\",\"totalPages\":2}\n"};
        server.createContext("/parse-stream", exchange -> {
            String request = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            assertTrue(request.contains("resume_pages"));
            byte[] bytes = payload[0].getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, 0);
            exchange.getResponseBody().write(bytes); exchange.close();
        });
        server.start();
        try {
            var client = new DoclingClient(new ObjectMapper(), "http://127.0.0.1:" + server.getAddress().getPort(), 5);
            var events = new java.util.ArrayList<String>();
            Path file = Files.writeString(dir.resolve("book.pdf"), "%PDF-test");
            client.stream(file, "book.pdf", 1, 1, "Chapter", e -> events.add(e.path("type").asText()));
            assertEquals(java.util.List.of("metadata", "batch", "complete"), events);
            payload[0] = "{\"type\":\"batch\"}\n";
            assertThrows(IllegalStateException.class, () -> client.stream(file, "book.pdf", 0, 0, "", e -> {}));
            payload[0] = "{\"type\":\"error\",\"retryable\":false,\"message\":\"bad document\"}\n";
            assertThrows(IllegalArgumentException.class, () -> client.stream(file, "book.pdf", 0, 0, "", e -> {}));
        } finally { server.stop(0); }
    }
    @Test void sendsMultipartAndRejectsEmptyOrPartialConversions(@TempDir Path dir) throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        String[] response = {"{\"blocks\":[{\"text\":\"正文\"}],\"warnings\":[]}"};
        server.createContext("/parse", exchange -> {
            String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            assertTrue(body.contains("filename=\"book.pdf\""));
            byte[] bytes = response[0].getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
        try {
            var client = new DoclingClient(new ObjectMapper(), "http://127.0.0.1:" + server.getAddress().getPort(), 5);
            Path file = Files.writeString(dir.resolve("book.pdf"), "%PDF-test");
            assertEquals(1, client.parse(file, "book.pdf").path("blocks").size());
            response[0] = "{\"blocks\":[]}";
            assertThrows(IllegalArgumentException.class, () -> client.parse(file, "book.pdf"));
            response[0] = "{\"blocks\":[{\"text\":\"正文\"}],\"partial\":true}";
            assertThrows(IllegalArgumentException.class, () -> client.parse(file, "book.pdf"));
        } finally { server.stop(0); }
    }
}
