package com.example.server.document;

import com.example.server.config.AuthInterceptor;
import com.example.server.controller.ApiExceptionHandler;
import com.example.server.service.AuthService;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import java.time.LocalDateTime;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class DocumentControllerTest {
    @Test void chunksRequireOwnership() throws Exception {
        when(repository.owned(ID, 8L)).thenThrow(new SecurityException("无权访问该文档"));
        mvc().perform(get("/documents/" + ID + "/chunks").header("Authorization", "Bearer other"))
                .andExpect(status().isForbidden());
        verify(repository, never()).chunks(anyString(), anyInt(), anyInt());
    }
    @Test void ownerCanPreviewRawChunksWithoutKnowledgeOutput() throws Exception {
        when(repository.owned(ID, 7L)).thenReturn(new DocumentRecord(ID, 7L, "book.pdf", 1, 1, "source",
                "COMPLETED", "READY", 1, 0, 1, null, null, "[]", LocalDateTime.now()));
        when(repository.chunks(ID, 0, 10)).thenReturn(java.util.List.of(new DocumentRepository.Block(
                0, "Chapter", "[{\"ref\":\"#/texts/1\",\"pages\":[1]}]", "original text", null)));
        mvc().perform(get("/documents/" + ID + "/chunks").header("Authorization", "Bearer owner"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.chunks[0].text").value("original text"))
                .andExpect(jsonPath("$.data.chunks[0].sources[0].pages[0]").value(1));
    }
    @Test void failuresRequireDocumentOwnership() throws Exception {
        when(repository.owned(ID, 8L)).thenThrow(new SecurityException("无权访问该文档"));
        mvc().perform(get("/documents/" + ID + "/failures").header("Authorization", "Bearer other"))
                .andExpect(status().isForbidden());
        verify(repository, never()).failures(anyString(), anyInt(), any());
    }
    private static final String ID = "91443c93-7f4b-43f8-91c0-22601ac83341";
    private final DocumentRepository repository = mock(DocumentRepository.class);
    private final DocumentQueue queue = mock(DocumentQueue.class);
    private MockMvc mvc() {
        AuthService auth = mock(AuthService.class);
        when(auth.resolveUser(null)).thenThrow(new SecurityException("请先登录"));
        when(auth.resolveUser("Bearer owner")).thenReturn(7L);
        when(auth.resolveUser("Bearer other")).thenReturn(8L);
        var mapper = new ObjectMapper().findAndRegisterModules();
        return MockMvcBuilders.standaloneSetup(new DocumentController(mock(DocumentUploadService.class), repository, queue, mapper,
                        new DocumentChatService(repository, mock(com.example.server.utils.DeepSeekUtils.class), mapper,
                                mock(org.redisson.api.RedissonClient.class)), Runnable::run))
                .addInterceptors(new AuthInterceptor(auth, mapper)).setControllerAdvice(new ApiExceptionHandler()).build();
    }
    @Test void anonymousRequestsCannotReadDocuments() throws Exception {
        mvc().perform(get("/documents")).andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").isNumber());
        verifyNoInteractions(repository, queue);
    }
    @Test void otherUserCannotReadReportOrRetryTask() throws Exception {
        when(repository.owned(ID, 8L)).thenThrow(new SecurityException("无权访问该文档"));
        var mvc = mvc();
        mvc.perform(get("/documents/" + ID + "/report").header("Authorization", "Bearer other")).andExpect(status().isForbidden());
        mvc.perform(post("/documents/" + ID + "/retry").header("Authorization", "Bearer other")).andExpect(status().isForbidden());
        verifyNoInteractions(queue);
    }
    @Test void ownerCanReadPersistedReportAfterRefresh() throws Exception {
        when(repository.owned(ID, 7L)).thenReturn(new DocumentRecord(ID, 7L, "book.pdf", 1, 1, "source",
                "COMPLETED", "COMPLETED", 1, 1, 1, null, "{\"title\":\"知识报告\"}", "[]", LocalDateTime.now()));
        mvc().perform(get("/documents/" + ID + "/report").header("Authorization", "Bearer owner"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.title").value("知识报告"));
    }
    @Test void otherUserCannotReadChatHistoryOrAsk() throws Exception {
        when(repository.owned(ID, 8L)).thenThrow(new SecurityException("无权访问该文档"));
        var mvc = mvc();
        mvc.perform(get("/documents/" + ID + "/chat").header("Authorization", "Bearer other"))
                .andExpect(status().isForbidden());
        var started = mvc.perform(post("/documents/" + ID + "/chat").header("Authorization", "Bearer other")
                        .contentType("application/json").content("{\"question\":\"内容是什么\",\"requestId\":\"" + ID + "\"}"))
                .andExpect(request().asyncStarted()).andReturn();
        mvc.perform(asyncDispatch(started)).andExpect(status().isForbidden());
        verify(repository, never()).chatHistory(any());
    }
    @Test void ownerCanReadSavedConversation() throws Exception {
        when(repository.chatHistory(ID)).thenReturn(java.util.List.of(new DocumentRepository.ChatTurn(ID,
                "文档是什么内容？", "{\"answer\":\"这是文档回答\",\"citations\":[]}")));
        mvc().perform(get("/documents/" + ID + "/chat").header("Authorization", "Bearer owner"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data[0].question").value("文档是什么内容？"))
                .andExpect(jsonPath("$.data[0].response.answer").value("这是文档回答"));
        verify(repository).owned(ID, 7L);
    }
}
