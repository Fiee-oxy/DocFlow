package com.example.server.document.adaptive;
import java.util.regex.Pattern;
/** Intent words inside quotations/code are data, not instructions for the router. */
final class RoutingText {
    private static final Pattern CODE=Pattern.compile("```.*?```|`[^`\\n]+`",Pattern.DOTALL);
    private static final Pattern QUOTED=Pattern.compile("\"[^\"\\n]*\"|“[^”\\n]*”|‘[^’\\n]*’|'[^'\\n]*'");
    static String outer(String q){return CODE.matcher(QUOTED.matcher(q).replaceAll(" QUOTED ")).replaceAll(" CODE ");}
    private RoutingText(){}
}
