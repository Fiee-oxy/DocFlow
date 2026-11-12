package com.example.server.document.adaptive;
import com.fasterxml.jackson.databind.*;
import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
class SectionQueryPlannerTest {
    final ObjectMapper mapper=new ObjectMapper();
    JsonNode section(String id,String path){return mapper.createObjectNode().put("section_id",id).put("title_path",path)
            .put("title",path.substring(path.lastIndexOf(" / ")+3));}
    @Test void sameLeafNamesAreDisambiguatedByProgramme(){
        var sections=List.of(section("a","计算机科学与技术专业培养方案 / 三、毕业要求"),
                section("b","数据科学与大数据技术专业培养方案 / 三、毕业要求"),
                section("c","数据科学与大数据技术专业培养方案 / 二、培养目标"));
        var plan=SectionQueryPlanner.plan("大数据专业毕业要求",sections);
        assertEquals(List.of("b"),plan.sectionIds());assertFalse(plan.ambiguous());
        assertTrue(SectionQueryPlanner.plan("毕业要求",sections).ambiguous());
        assertTrue(SectionQueryPlanner.plan("Redis是什么意思",sections).sectionIds().isEmpty());
    }
    @Test void chapterExpansionNeverIncludesSiblingGoals(){
        var sections=List.of(section("b","大数据专业培养方案 / 三、毕业要求"),
                section("child","大数据专业培养方案 / 三、毕业要求 / 工程知识"),
                section("goals","大数据专业培养方案 / 二、培养目标"));
        assertEquals(List.of("b","child"),SectionQueryPlanner.plan("大数据专业毕业要求",sections).sectionIds());
    }
    @Test void quantitativeQuestionUsesSameProgrammeSiblingsInsteadOfOnlyTheNamedChapter(){
        var sections=List.of(section("g","大数据专业培养方案 / 三、毕业要求"),section("credit","大数据专业培养方案 / 八、毕业最低学分"),
                section("other","软件工程专业培养方案 / 八、毕业最低学分"));
        var plan=SectionQueryPlanner.plan("大数据专业毕业要求修满多少学分",sections);
        assertEquals(List.of("g","credit"),plan.sectionIds());assertTrue(plan.specificFact());assertEquals("SUBJECT_FACT",plan.scopeType());
        assertEquals("大数据专业修满多少学分",plan.searchQuestion());
        var explicit=SectionQueryPlanner.plan("只根据大数据专业的毕业要求，修满多少学分",sections);
        assertEquals(List.of("g"),explicit.sectionIds());assertEquals("EXPLICIT_CHAPTER",explicit.scopeType());
        var documentOnly=SectionQueryPlanner.plan("只根据这个文档回答，大数据专业毕业要求修满多少学分",sections);
        assertEquals(List.of("g","credit"),documentOnly.sectionIds());
    }
    @Test void nestedSubjectScopeNeverWidensToTheWholeSchool(){
        var sections=List.of(section("g","全校培养方案 / 大数据专业 / 三、毕业要求"),
                section("c","全校培养方案 / 大数据专业 / 八、毕业最低学分"),
                section("other","全校培养方案 / 软件工程专业 / 八、毕业最低学分"));
        var plan=SectionQueryPlanner.plan("大数据专业毕业要求修满多少学分",sections);
        assertEquals(List.of("g","c"),plan.sectionIds());assertEquals("全校培养方案 / 大数据专业",plan.scopeTitlePath());
    }
    @Test void namedMetricsUseSubjectSiblingsWithoutQuantitativeQuestionWords(){
        var sections=List.of(section("g","大数据专业培养方案 / 三、毕业要求"),
                section("credit","大数据专业培养方案 / 八、毕业最低学分"),
                section("other","软件工程专业培养方案 / 八、毕业最低学分"));
        for(String question:List.of("大数据专业毕业要求的学分要求是什么","介绍大数据专业毕业要求（包括学分）",
                "大数据专业毕业要求中的费用","大数据专业毕业要求的期限","大数据专业毕业要求的日期",
                "大数据专业毕业要求的年限")){
            var plan=SectionQueryPlanner.plan(question,sections);
            assertEquals(List.of("g","credit"),plan.sectionIds(),question);
            assertTrue(plan.specificFact(),question);assertEquals("SUBJECT_FACT",plan.scopeType(),question);
        }
        var overview=SectionQueryPlanner.plan("介绍大数据专业毕业要求，包括毕业条件",sections);
        assertEquals(List.of("g"),overview.sectionIds());assertFalse(overview.specificFact());
        assertEquals("CHAPTER_OVERVIEW",overview.scopeType());
        var explicit=SectionQueryPlanner.plan("只根据大数据专业的毕业要求介绍学分要求",sections);
        assertEquals(List.of("g"),explicit.sectionIds());assertEquals("EXPLICIT_CHAPTER",explicit.scopeType());
    }
    @Test void metricChapterNameIsNotDeletedIntoAnEmptyQueryAndNoParentDoesNotExpand(){
        var sections=List.of(section("credits","大数据专业 / 最低学分"));
        assertEquals("大数据专业最低学分是多少",SectionQueryPlanner.plan("大数据专业最低学分是多少",sections).searchQuestion());
        var root=mapper.createObjectNode().put("title","毕业要求").put("title_path","毕业要求").put("section_id","only");
        var noParent=SectionQueryPlanner.plan("毕业要求修满多少学分",List.of(root));
        assertEquals(List.of("only"),noParent.sectionIds());assertEquals("CHAPTER_PARENT_UNAVAILABLE",noParent.scopeType());
    }
}
