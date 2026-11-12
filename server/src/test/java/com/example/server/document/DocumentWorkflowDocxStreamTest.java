package com.example.server.document;

import com.example.server.document.adaptive.AdaptiveDocumentRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.test.util.ReflectionTestUtils;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class DocumentWorkflowDocxStreamTest {
    @TempDir Path directory;
    private static final String ID="91443c93-7f4b-43f8-91c0-22601ac83341";
    private final ObjectMapper mapper=new ObjectMapper();
    private final DocumentRepository documents=mock(DocumentRepository.class);
    private final DocumentUploadService uploads=mock(DocumentUploadService.class);
    private final DoclingClient parser=mock(DoclingClient.class);
    private final AdaptiveDocumentRepository adaptive=mock(AdaptiveDocumentRepository.class);
    private final RedissonClient redisson=mock(RedissonClient.class);
    private final RLock lock=mock(RLock.class);

    private DocumentRecord doc(int batches,int blocks) {
        return new DocumentRecord(ID,7L,"book.docx",10,1,"source","RETRYING","PARSING",blocks,0,
                1,null,null,"[]",LocalDateTime.now(),0,0,false,batches);
    }
    private DocumentWorkflow workflow(int batches,int blocks) throws Exception {
        when(documents.get(ID)).thenReturn(doc(batches,blocks));
        when(documents.blocks(ID)).thenReturn(List.of());
        when(redisson.getLock(anyString())).thenReturn(lock);
        when(lock.tryLock()).thenReturn(true);
        when(lock.isHeldByCurrentThread()).thenReturn(true);
        when(uploads.materialize(any())).thenReturn(Files.writeString(directory.resolve("book.docx"),"fixture"));
        var workflow=new DocumentWorkflow(documents,uploads,parser,mock(DocumentChunker.class),redisson);
        workflow.adaptiveIngestion(adaptive);
        ReflectionTestUtils.setField(workflow,"docxParser","DOCX_STREAM");
        return workflow;
    }
    private String state(int nextNode,int nextBatch) {
        return "{\"version\":1,\"parser\":\"DOCX_STREAM\",\"nextBodyNode\":"+nextNode+",\"nextBatch\":"+nextBatch+",\"heading\":[\"Chapter\"]}";
    }
    private JsonNode batch(int index,int start,int end) throws Exception {
        return mapper.readTree("{\"type\":\"batch\",\"parser\":\"DOCX_STREAM\",\"parseUnit\":\"DOCX_BODY_NODE\",\"batchIndex\":"+index+
                ",\"totalPages\":0,\"startPage\":0,\"endPage\":0,\"startNode\":"+start+",\"endNode\":"+end+
                ",\"blocks\":[{\"text\":\"node text\",\"body_node\":"+start+",\"pages\":[]}],\"warnings\":[],\"docxResumeState\":"+state(end,index+1)+"}");
    }
    private JsonNode complete(int end) throws Exception {
        return mapper.readTree("{\"type\":\"complete\",\"parseUnit\":\"DOCX_BODY_NODE\",\"totalPages\":0,\"totalBodyNodes\":"+end+",\"endNode\":"+end+"}");
    }

    @Test void keepsDefaultAndExistingDoclingTasksWhilePinningStreamingResume() throws Exception {
        var workflow=workflow(0,0);
        ReflectionTestUtils.setField(workflow,"docxParser","DOCLING");
        assertEquals("DOCLING",workflow.parserMode(doc(0,0),null));
        ReflectionTestUtils.setField(workflow,"docxParser","DOCX_STREAM");
        assertEquals("DOCX_STREAM",workflow.parserMode(doc(0,0),null));
        assertEquals("DOCLING",workflow.parserMode(doc(1,1),null));
        ReflectionTestUtils.setField(workflow,"docxParser","DOCLING");
        assertEquals("DOCX_STREAM",workflow.parserMode(doc(1,1),state(32,1)));
    }
    @Test void pinsAdaptiveModeToCommittedContractDespiteDefaultChange() throws Exception {
        var workflow=workflow(1,1);
        ReflectionTestUtils.setField(workflow,"docxParser","DOCLING");
        assertEquals("AUTO",workflow.parserMode(doc(1,1),"{\"contract\":{\"mode\":\"AUTO\"}}"));
        assertEquals("HIGH_ACCURACY",workflow.parserMode(doc(1,1),"{\"contract\":{\"mode\":\"HIGH_ACCURACY\"}}"));
    }
    @Test void commitsIncrementalBodyNodesWithoutInventingPages() throws Exception {
        var workflow=workflow(0,0);
        var first=batch(0,0,32);var second=batch(1,32,47);
        var units=List.of(new DocumentRepository.Block(0,"Chapter","[]","node text",null));
        when(adaptive.sourceUnits(any(),anyInt(),anyString())).thenReturn(units);
        doAnswer(call->{
            var consumer=call.getArgument(6,DoclingClient.EventConsumer.class);
            consumer.accept(first);
            verify(adaptive).commitBatch(eq(ID),eq(0),eq(units),eq(0),eq(0),eq("[]"),eq(first));
            verify(documents,never()).parsed(anyString());
            consumer.accept(second);consumer.accept(complete(47));return null;
        }).when(parser).stream(any(),eq("book.docx"),eq(0),eq(0),eq(""),eq("DOCX_STREAM"),any());
        workflow.process(ID);
        verify(adaptive).commitBatch(eq(ID),eq(1),eq(units),eq(0),eq(0),eq("[]"),eq(second));
        verify(documents).parsed(ID);verify(adaptive).finish(ID);
        verify(documents,never()).failed(anyString(),anyString(),anyBoolean());
    }
    @Test void resumesNodeCursorFromCommittedJsonEvenAfterDefaultChanges() throws Exception {
        var workflow=workflow(1,1);
        ReflectionTestUtils.setField(workflow,"docxParser","DOCLING");
        String checkpoint=state(32,1);
        when(adaptive.resumeState(ID,0)).thenReturn(checkpoint);
        var next=batch(1,32,47);
        when(adaptive.sourceUnits(any(),anyInt(),anyString())).thenReturn(List.of());
        doAnswer(call->{var consumer=call.getArgument(7,DoclingClient.EventConsumer.class);
            consumer.accept(next);consumer.accept(complete(47));return null;
        }).when(parser).stream(any(),eq("book.docx"),eq(0),eq(1),eq(""),eq("DOCX_STREAM"),eq(checkpoint),any());
        workflow.process(ID);
        verify(adaptive).commitBatch(eq(ID),eq(1),eq(List.of()),eq(0),eq(0),eq("[]"),eq(next));
        verify(documents).parsed(ID);
    }
    @Test void rejectsSkippedNodesOrMismatchedCompletionInsteadOfPublishingPartialDocument() throws Exception {
        var workflow=workflow(0,0);
        var invalid=batch(0,2,32);
        doAnswer(call->{call.getArgument(6,DoclingClient.EventConsumer.class).accept(invalid);return null;})
                .when(parser).stream(any(),anyString(),eq(0),eq(0),anyString(),eq("DOCX_STREAM"),any());
        workflow.process(ID);
        verify(adaptive,never()).commitBatch(anyString(),anyInt(),anyList(),anyInt(),anyInt(),anyString(),any());
        verify(documents).failed(eq(ID),anyString(),eq(false));
        verify(documents,never()).parsed(anyString());
    }
    @Test void cannotFinishBeforeAllBodyNodesAreCommitted() throws Exception {
        var workflow=workflow(0,0);var first=batch(0,0,32);
        when(adaptive.sourceUnits(any(),anyInt(),anyString())).thenReturn(List.of());
        doAnswer(call->{var consumer=call.getArgument(6,DoclingClient.EventConsumer.class);
            consumer.accept(first);consumer.accept(complete(47));return null;})
                .when(parser).stream(any(),anyString(),eq(0),eq(0),anyString(),eq("DOCX_STREAM"),any());
        workflow.process(ID);
        verify(adaptive).commitBatch(eq(ID),eq(0),anyList(),eq(0),eq(0),eq("[]"),eq(first));
        verify(documents).failed(eq(ID),anyString(),eq(false));
        verify(documents,never()).parsed(anyString());
    }
    @Test void finalCommittedCheckpointCanFinishWithoutReplayingNodes() throws Exception {
        var workflow=workflow(2,4);String checkpoint=state(47,2);
        when(adaptive.resumeState(ID,1)).thenReturn(checkpoint);
        doAnswer(call->{call.getArgument(7,DoclingClient.EventConsumer.class).accept(complete(47));return null;})
                .when(parser).stream(any(),anyString(),eq(0),eq(2),anyString(),eq("DOCX_STREAM"),eq(checkpoint),any());
        workflow.process(ID);
        verify(documents).parsed(ID);
        verify(adaptive,never()).commitBatch(anyString(),anyInt(),anyList(),anyInt(),anyInt(),anyString(),any());
    }
    @Test void partialBatchPersistsUnsupportedWarningAndNeverPublishesThatBatch() throws Exception {
        var workflow=workflow(0,0);
        var partial=(com.fasterxml.jackson.databind.node.ObjectNode)batch(0,0,32);
        partial.put("partial",true).putArray("warnings").add("DOCX_UNSUPPORTED_IMAGE: use Docling");
        doAnswer(call->{call.getArgument(6,DoclingClient.EventConsumer.class).accept(partial);return null;})
                .when(parser).stream(any(),anyString(),eq(0),eq(0),anyString(),eq("DOCX_STREAM"),any());
        workflow.process(ID);
        verify(documents).parserWarnings(eq(ID),contains("DOCX_UNSUPPORTED_IMAGE"));
        verify(documents).failed(eq(ID),anyString(),eq(false));
        verify(documents,never()).parsed(anyString());
        verify(adaptive,never()).commitBatch(anyString(),anyInt(),anyList(),anyInt(),anyInt(),anyString(),any());
        verify(adaptive,never()).finish(anyString());
    }
    @Test void partialCompletionRetainsEarlierBatchButCannotMarkDocumentComplete() throws Exception {
        var workflow=workflow(0,0);var first=batch(0,0,32);
        var partial=(com.fasterxml.jackson.databind.node.ObjectNode)complete(32);
        partial.put("partial",true).putArray("parseWarnings").add("Unsupported picture was skipped");
        when(adaptive.sourceUnits(any(),anyInt(),anyString())).thenReturn(List.of());
        doAnswer(call->{var consumer=call.getArgument(6,DoclingClient.EventConsumer.class);
            consumer.accept(first);consumer.accept(partial);return null;})
                .when(parser).stream(any(),anyString(),eq(0),eq(0),anyString(),eq("DOCX_STREAM"),any());
        workflow.process(ID);
        verify(adaptive).commitBatch(eq(ID),eq(0),anyList(),eq(0),eq(0),eq("[]"),eq(first));
        verify(documents).parserWarnings(eq(ID),contains("Unsupported picture was skipped"));
        verify(documents).failed(eq(ID),anyString(),eq(false));
        verify(documents,never()).parsed(anyString());
    }
    @Test void unsupportedCheckpointCannotBeCompletedAfterRestartEvenWithoutPartialFlag() throws Exception {
        var workflow=workflow(1,1);
        var checkpoint=(com.fasterxml.jackson.databind.node.ObjectNode)mapper.readTree(state(32,1));
        checkpoint.putArray("warnings").add("DOCX_UNSUPPORTED_FORMULA: use Docling");
        when(adaptive.resumeState(ID,0)).thenReturn(checkpoint.toString());
        workflow.process(ID);
        verifyNoInteractions(parser);
        verify(documents).parserWarnings(eq(ID),contains("DOCX_UNSUPPORTED_FORMULA"));
        verify(documents).failed(eq(ID),anyString(),eq(false));
        verify(documents,never()).parsed(anyString());
    }
    @Test void completionOnlyWarningsAreSavedAndOrdinaryCapabilityNoticeIsNotPartial() throws Exception {
        var workflow=workflow(0,0);
        var first=(com.fasterxml.jackson.databind.node.ObjectNode)batch(0,0,32);
        first.putArray("warnings").add("Experimental DOCX streaming does not supply page numbers");
        var completed=(com.fasterxml.jackson.databind.node.ObjectNode)complete(32);
        completed.putArray("warnings").add("Final informational notice");
        when(adaptive.sourceUnits(any(),anyInt(),anyString())).thenReturn(List.of());
        doAnswer(call->{var consumer=call.getArgument(6,DoclingClient.EventConsumer.class);
            consumer.accept(first);consumer.accept(completed);return null;})
                .when(parser).stream(any(),anyString(),eq(0),eq(0),anyString(),eq("DOCX_STREAM"),any());
        workflow.process(ID);
        verify(documents).parserWarnings(eq(ID),argThat(json->json.contains("Final informational notice") && json.contains("does not supply page numbers")));
        verify(documents).parsed(ID);
        verify(documents,never()).failed(anyString(),anyString(),anyBoolean());
    }
}
