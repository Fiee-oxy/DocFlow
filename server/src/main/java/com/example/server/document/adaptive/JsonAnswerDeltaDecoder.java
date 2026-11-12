package com.example.server.document.adaptive;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.regex.Pattern;

/** Emits only decoded answer string content; incomplete JSON escapes wait for the next delta. */
public class JsonAnswerDeltaDecoder {
    private final ObjectMapper mapper;
    private final StringBuilder buffer=new StringBuilder();
    private String emitted="";
    public JsonAnswerDeltaDecoder(ObjectMapper mapper){this.mapper=mapper;}
    public String accept(String piece)throws Exception{
        buffer.append(piece);if(buffer.length()>128000)throw new IllegalStateException("模型输出超过限制");
        var matcher=Pattern.compile("\"answer\"\\s*:\\s*\"").matcher(buffer);
        if(!matcher.find())return "";
        int start=matcher.end(),end=start;
        while(end<buffer.length()){
            char c=buffer.charAt(end);if(c=='"')break;
            if(c=='\\'){
                if(end+1>=buffer.length())break;
                if(buffer.charAt(end+1)=='u'){if(end+6>buffer.length())break;end+=6;}
                else end+=2;
            }else end++;
        }
        String decoded=mapper.readTree("\""+buffer.substring(start,end)+"\"").asText();
        // Hold a trailing high surrogate until its matching low surrogate arrives.
        if(!decoded.isEmpty()&&Character.isHighSurrogate(decoded.charAt(decoded.length()-1)))decoded=decoded.substring(0,decoded.length()-1);
        if(!decoded.startsWith(emitted))throw new IllegalStateException("模型流式回答前缀不一致");
        String delta=decoded.substring(emitted.length());emitted=decoded;return delta;
    }
}
