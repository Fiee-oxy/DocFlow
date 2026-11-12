package com.example.server.document.parsing;

import com.fasterxml.jackson.databind.JsonNode;

/** All views are derived from the saved authoritative JSON, never separate facts. */
public final class DocumentIRCodec {
    private DocumentIRCodec(){}
    public static String export(JsonNode ir,String format){
        if(!"1.0".equals(ir.path("schemaVersion").asText())||!ir.path("blocks").isArray())throw new IllegalArgumentException("不支持的 Document IR");
        if("json".equals(format))return ir.toPrettyString();
        var out=new StringBuilder();
        for(var block:ir.path("blocks")){
            if("jsonl".equals(format))out.append(block.toString()).append('\n');
            else if("markdown".equals(format)){
                out.append("<!-- Block:").append(block.path("blockId").asText()).append(" -->\n");
                if("HEADING".equals(block.path("type").asText()))out.append("#".repeat(Math.max(1,Math.min(6,block.path("level").asInt(1))))).append(' ');
                out.append(block.path("text").asText()).append("\n\n");
            }else throw new IllegalArgumentException("format 须为 json、jsonl 或 markdown");
        }
        return out.toString();
    }
}
