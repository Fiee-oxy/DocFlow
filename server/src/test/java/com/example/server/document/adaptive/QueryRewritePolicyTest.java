package com.example.server.document.adaptive;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class QueryRewritePolicyTest {
    private final ObjectMapper mapper=new ObjectMapper();
    @Test void acceptsAliasesWithoutReplacingOriginal()throws Exception{
        assertEquals(java.util.List.of("3M capex fiscal year 2018 USD millions"),QueryRewritePolicy.validate(
            "What was 3M capital expenditure in FY2018, in USD millions?",mapper.readTree("{\"queries\":[\"3M capex fiscal year 2018 USD millions\"]}")));
    }
    @Test void rejectsInventedNumbersAndChangedLiteral()throws Exception{
        assertTrue(QueryRewritePolicy.validate("find `Error42` in 2024",mapper.readTree("{\"queries\":[\"Error43 in 2024\",\"Error42 in 2025\"]}")).isEmpty());
        assertTrue(QueryRewritePolicy.validate("find /api/users",mapper.readTree("{\"queries\":[\"find /api/accounts\"]}")).isEmpty());
    }
    @Test void refusesUnboundedOutput()throws Exception{
        assertThrows(IllegalArgumentException.class,()->QueryRewritePolicy.validate("query",mapper.readTree("{\"queries\":[\"a\",\"b\",\"c\"]}")));
        assertTrue(QueryRewritePolicy.validate("same",mapper.readTree("{\"queries\":[\"same\",123]}")).isEmpty());
    }
}
