package com.example.server.document.adaptive;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.*;

/** Exact chapter names constrain retrieval; subject disambiguation never guesses a page number. */
public final class SectionQueryPlanner {
    private SectionQueryPlanner(){}
    public record Plan(List<String> sectionIds,String titlePath,boolean ambiguous,List<String> alternatives,
            boolean specificFact,String searchQuestion,String scopeType,String scopeTitlePath){
        public Plan(List<String> ids,String path,boolean ambiguous,List<String> alternatives){
            this(ids,path,ambiguous,alternatives,false,"","CHAPTER",path);
        }
    }
    private static boolean specificFact(String question,String chapter){
        String remainder=question.replace(chapter,"");
        // A named metric can need a sibling chapter even without a quantitative question word.
        return remainder.matches("(?s).*(多少|几门|几个|几年|几学分|多久|多长|何时|什么时候|哪里|是否|能否|需不需要|修满|至少|最低|最高|总计|总共|上限|下限|不低于|不超过|截止日期|联系方式|学分|学费|费用|金额|期限|日期|年限|学制|时长|课时|学时|人数|比例).*" );
    }
    private static boolean explicitChapter(String question,String chapter){
        String title=java.util.regex.Pattern.quote(chapter);
        var scoped=java.util.regex.Pattern.compile("(只根据|仅根据|只看|仅限|限定在)\\s*([^，,。；;\\n]{0,60}?)"+title).matcher(question);
        while(scoped.find()){
            String prefix=scoped.group(2).replaceAll("[“\"《\\s一二三四五六七八九十百0-9、．.]","");
            if(prefix.isEmpty()||prefix.contains("专业")&&!prefix.matches(".*(文档|文件|回答|告诉).*"))return true;
        }
        return question.matches("(?s).*(只根据|仅根据|只看|仅限|限定在)\\s*[“\"《]?(?:[一二三四五六七八九十百0-9]+[、．.]\\s*)?"+title+".*")
                ||question.matches("(?s).*"+title+"[”\"》]?\\s*(这一章|这一节|章节内|章内|节内).*" )
                ||question.matches("(?is).*(第[一二三四五六七八九十百0-9]+[章节]|chapter\\s+\\d+|section\\s+\\d+).*" );
    }
    public static String clean(String title){
        return title.replaceFirst("^(第[一二三四五六七八九十百0-9]+[章节]|[一二三四五六七八九十百0-9]+[、．.])\\s*","").strip();
    }
    public static boolean enumerative(String q){return q.matches("(?s).*(要求|条件|步骤|流程|课程|清单|列出|有哪些|包括哪些|汇总).*" );}
    public static Plan plan(String question,List<JsonNode> sections){
        var matches=sections.stream().filter(s->{String title=clean(s.path("title").asText());
            return title.length()>=2&&!title.equals("正文")&&question.contains(title);}).toList();
        if(matches.isEmpty())return new Plan(List.of(),"",false,List.of(),false,question,"UNRESTRICTED","");
        int longest=matches.stream().mapToInt(s->clean(s.path("title").asText()).length()).max().orElse(0);
        matches=matches.stream().filter(s->clean(s.path("title").asText()).length()==longest).toList();
        int best=-1;var winners=new ArrayList<JsonNode>();
        for(var s:matches){String path=s.path("title_path").asText(),leaf=clean(s.path("title").asText());
            String subject=question.replace(leaf,"").replaceAll("专业|文档|文件|请问|请|介绍|说明|哪些|什么|有哪些|包括|的|？|\\?","");
            String parent=path.contains(" / ")?path.substring(0,path.lastIndexOf(" / ")):"";
            int score=0;for(int i=0;i+1<subject.length();i++)if(parent.contains(subject.substring(i,i+2)))score++;
            if(score>best){best=score;winners.clear();}if(score==best)winners.add(s);
        }
        if(winners.size()!=1)return new Plan(List.of(),"",true,winners.stream().map(s->s.path("title_path").asText()).distinct().toList());
        String path=winners.getFirst().path("title_path").asText();
        String chapter=clean(winners.getFirst().path("title").asText());
        boolean fact=specificFact(question,chapter),explicit=explicitChapter(question,chapter);
        String scope=path,scopeType=explicit?"EXPLICIT_CHAPTER":"CHAPTER_OVERVIEW";
        if(fact&&!explicit&&path.contains(" / ")){
            scope=path.substring(0,path.lastIndexOf(" / "));scopeType="SUBJECT_FACT";
            // Use the nearest named programme ancestor when the matched leaf is nested more deeply.
            String programme="";
            for(var section:sections){String candidate=section.path("title_path").asText();
                if(section.path("title").asText().contains("专业")&&path.startsWith(candidate+" / ")&&candidate.length()>programme.length())programme=candidate;
            }
            if(!programme.isEmpty())scope=programme;
        }else if(fact)scopeType=explicit?"EXPLICIT_CHAPTER":"CHAPTER_PARENT_UNAVAILABLE";
        final String prefix=scope;
        String search=fact?question.replace(chapter,"").strip():question;
        String object=search.replaceAll("请问|告诉我|帮我|需不需要|什么时候|不低于|不超过|是否|能否|需要|修满|至少|最低|最高|总计|总共|多少|几个|几门|几年|多久|多长|何时|哪里|专业|的|是|请","");
        final String scopePath=scope;
        boolean hasObject=object.codePoints().anyMatch(c->Character.isLetterOrDigit(c)&&!scopePath.contains(new String(Character.toChars(c))));
        if(search.isBlank()||!hasObject)search=question;
        return new Plan(sections.stream().filter(s->s.path("title_path").asText().equals(prefix)||s.path("title_path").asText().startsWith(prefix+" / "))
                .map(s->s.path("section_id").asText()).distinct().toList(),path,false,List.of(),fact,search,scopeType,scope);
    }
}
