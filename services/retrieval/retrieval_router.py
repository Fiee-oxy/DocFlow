"""Versioned routing and deterministic single-channel quality checks. No models."""
import hashlib
import math
import os
import re
import time
import unicodedata
from dataclasses import asdict,dataclass
from pathlib import Path

REVISION="retrieval-router-v2"
QUALITY_REVISION="retrieval-quality-v1"
DEFAULT_PATH=Path(__file__).resolve().parent.parent.parent/"config/retrieval-routing-rules.yml"
if not DEFAULT_PATH.exists():DEFAULT_PATH=Path(__file__).with_name("config")/"retrieval-routing-rules.yml"


def normalized(text):return unicodedata.normalize("NFKC",text).casefold()


class RuleDictionary:
    def __init__(self,path=None):
        import yaml
        raw=Path(path or os.getenv("RETRIEVAL_ROUTING_RULES",str(DEFAULT_PATH))).read_bytes()
        self.sha=hashlib.sha256(raw).hexdigest();data=yaml.safe_load(raw)
        if data.get("version")!=REVISION:raise ValueError("Routing dictionary version mismatch")
        self.patterns={}
        for name in ("literal","exact_constraint","semantic","comparison","multi","global","metadata","section_read","negation","followup"):
            values=data.get(name)
            if not isinstance(values,list) or not values or any(not isinstance(v,str) or not v for v in values):
                raise ValueError("Invalid routing dictionary: "+name)
            self.patterns[name]=re.compile("|".join((r"(?<!\w)"+re.escape(normalized(v))+r"(?!\w)") if v.isascii() else re.escape(normalized(v)) for v in values))

    def has(self,name,text):return bool(self.patterns[name].search(text))


_rules=None
def rules():
    global _rules
    if _rules is None:_rules=RuleDictionary()
    return _rules


QUOTES=re.compile(r'"([^"\n]{1,2000})"|“([^”\n]{1,2000})”|‘([^’\n]{1,2000})’|(?<!\w)\'([^\'\n]{1,2000})\'(?!\w)')
CODE=re.compile(r"```.*?```|`[^`\n]+`",re.S)
SECTION=re.compile(r"第[一二三四五六七八九十百0-9]+[章节]|\b(?:chapter|section)\s+\d+(?:\.\d+)*\b",re.I)
IDENTIFIERS=tuple(re.compile(p) for p in (
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    r"(?i)\b0x[0-9a-f]+\b",r"(?i)(?:http(?:\s+status)?|status\s+code|状态码)\s*[:：]?\s*[1-5]\d{2}\b",
    r"(?i)\b[\w.-]+\.(?:pdf|docx?|xlsx?|csv|html?|json|xml|ya?ml|java|py|txt|md)\b",
    r"(?i)(?:[a-z]:\\|/)(?:[\w.{}-]+[/\\])+[\w.{}-]*",r"(?i)/(?:api|v\d+)/[\w/{}.-]+",
    r"\b[A-Z][A-Za-z0-9]*(?:Service|Controller|Repository|Exception|Client|Manager)(?:\.[A-Za-z_]\w*(?:\(\))?)?\b",
    r"(?i)\b(?:rfc|iso|iec|gb)\s*[-/]?\s*\d+(?:[-.:/]\d+)*\b",r"(?i)\b[a-z]+[-_]\d+[a-z0-9_-]*\b"))


@dataclass(frozen=True)
class QueryFeatures:
    hasExactIdentifier:bool
    hasQuotedPhrase:bool
    hasLiteralLookupIntent:bool
    hasSemanticSimilarityIntent:bool
    hasExactTermConstraint:bool
    hasComparisonIntent:bool
    hasMultiEvidenceIntent:bool
    hasGlobalAnalysisIntent:bool
    hasMetadataIntent:bool
    hasSectionReference:bool
    queryTokenCount:int
    exactIdentifiers:list
    sectionReferences:list
    quotedPhrases:list
    sectionReadIntent:bool
    ambiguous:bool


def analyze(question,dictionary=None):
    dictionary=dictionary or rules();original=unicodedata.normalize("NFKC",question);q=original.casefold()
    quoted=[next(g for g in match.groups() if g is not None) for match in QUOTES.finditer(q)]
    outer=CODE.sub(" CODE ",QUOTES.sub(" QUOTED ",q))
    identifiers=list(dict.fromkeys(m.group(0) for pattern in IDENTIFIERS for m in pattern.finditer(original)))
    sections=list(dict.fromkeys(m.group(0) for m in SECTION.finditer(outer)))
    literal=dictionary.has("literal",outer);semantic=dictionary.has("semantic",outer)
    return QueryFeatures(bool(identifiers),bool(quoted),literal,semantic,dictionary.has("exact_constraint",outer),
        dictionary.has("comparison",outer),dictionary.has("multi",outer),dictionary.has("global",outer),dictionary.has("metadata",outer),
        bool(sections),len(re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]",q)),identifiers,sections,quoted,
        dictionary.has("section_read",outer) or bool(re.search(r"(?:读取|给出|显示).*(?:章|节).*原文",outer)),
        dictionary.has("negation",outer) or dictionary.has("followup",outer))


def plan(question,context=None,enabled_rules=None):
    context=context or {};enabled_rules=enabled_rules or {};start=time.perf_counter()
    f=analyze(question);analysis_ms=(time.perf_counter()-start)*1000;start=time.perf_counter()
    enabled=lambda name:enabled_rules.get(name,True)
    mode,rule,reason="HYBRID","R999","AMBIGUOUS_OR_DEFAULT_HYBRID"
    grounded=context.get("documentGrounded",True);attrs=context.get("attributes",{})
    if not grounded and context.get("generalChatMode",False) and not context.get("documentIds") and enabled("general-knowledge"):
        mode,rule,reason="NONE","R001","EXPLICIT_GENERAL_CHAT_WITHOUT_DOCUMENT_SCOPE"
    elif f.hasGlobalAnalysisIntent:
        reason="DELEGATE_EXISTING_GLOBAL_ANALYSIS"
    elif f.ambiguous:
        reason="NEGATION_OR_UNRESOLVED_REFERENCE"
    elif f.hasMetadataIntent and attrs.get("metadataQuerySupported",False) and enabled("metadata"):
        mode,rule,reason="STRUCTURED","R002","SUPPORTED_OWNED_DOCUMENT_METADATA"
    elif f.sectionReadIntent and f.hasSectionReference and attrs.get("resolvedSectionIds") and enabled("section-read"):
        mode,rule,reason="SECTION_READ","R003","EXPLICIT_RESOLVED_SECTION_ORIGINAL"
    elif f.hasComparisonIntent or f.hasMultiEvidenceIntent or f.hasLiteralLookupIntent and f.hasSemanticSimilarityIntent and (f.hasExactIdentifier or f.hasQuotedPhrase or f.hasExactTermConstraint):
        rule,reason="R004","MULTI_EVIDENCE_OR_MIXED_INTENT"
    elif f.hasLiteralLookupIntent and (f.hasExactIdentifier or f.hasQuotedPhrase or f.hasExactTermConstraint) and enabled("exact-lookup"):
        mode,rule,reason="BM25","R005","EXPLICIT_LITERAL_LOOKUP"
    elif f.hasSemanticSimilarityIntent and enabled("semantic-similarity"):
        mode,rule,reason="DENSE","R006","EXPLICIT_SEMANTIC_SIMILARITY"
    scope=dict(context.get("scope",{}));scope["sectionReferences"]=f.sectionReferences
    return dict(revision=REVISION,ruleVersion=REVISION,dictionaryVersion=rules().sha,initialRoute=mode,mode=mode,
        reason=reason,reasonCode=reason,matchedRules=[rule],fallbackEnabled=mode in ("BM25","DENSE"),scope=scope,
        features=asdict(f),timingMs=dict(analyzer=analysis_ms,ruleEngine=(time.perf_counter()-start)*1000))


STOP_WORDS=set('a an the is are was were be been to of in on for and or with from by as it its this that what which where when why how find exact locate contains search text semantically similar meaning passages passage about describe describing please'.split())

def _terms(question):
    text=normalized(question)
    text=re.sub(r"搜索包含|原文出现|精确匹配|查找|定位|找到|意思相近|语义相似|类似表达|相近描述|请问|请|为什么|什么|哪些|如何|的|了", " ", text)
    result=[]
    for word in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+",text):
        if word.isascii():
            if word not in STOP_WORDS:result.append(word)
        else:result.extend(word[i:i+2] for i in range(len(word)-1)) if len(word)>1 else result.append(word)
    return list(dict.fromkeys(result))[:128]

def _contains(text,term):
    if term.isascii() and re.match(r"^\w",term) and re.search(r"\w$",term):
        return bool(re.search(r"(?<!\w)"+re.escape(term)+r"(?!\w)",text))
    return term in text

def assess(question,hits,route,*,bm25_min_coverage=.55,dense_min_score=.45,quality_top_k=5):
    if (not math.isfinite(bm25_min_coverage) or not 0<=bm25_min_coverage<=1 or
            not math.isfinite(dense_min_score) or not -1<=dense_min_score<=1 or not 1<=quality_top_k<=20):
        raise ValueError("Invalid retrieval quality thresholds")
    quality=dict(accepted=False,reason="EMPTY_RESULT",reasons=["EMPTY_RESULT"],policy=QUALITY_REVISION,
        route=route,skipRerank=False,candidateCount=len(hits),
        thresholds=dict(bm25MinCoverage=bm25_min_coverage,denseMinScore=dense_min_score,qualityTopK=quality_top_k))
    if not hits:return quality
    samples=hits[:quality_top_k];scores=[]
    for hit in samples:
        try:score=float(hit.get('score'))
        except (ValueError,TypeError):continue
        if math.isfinite(score):scores.append(score)
    scores.sort(reverse=True)
    quality.update(topScore=scores[0] if scores else None,
        topScoreGap=scores[0]-scores[1] if len(scores)>1 else None,sampledCandidates=len(samples))
    reasons=[]
    if route=='BM25':
        q=unicodedata.normalize('NFKC',question)
        anchors=[next(g for g in match.groups() if g is not None) for match in QUOTES.finditer(q)]
        anchors+=list(dict.fromkeys(m.group(0) for pattern in IDENTIFIERS for m in pattern.finditer(q)))
        anchors=list(dict.fromkeys(re.sub(r'\s+',' ',normalized(a)).strip() for a in anchors))
        terms=_terms(question);best=None
        for hit in samples:
            text=re.sub(r'\s+',' ',normalized(str(hit.get('content') or hit.get('search_text') or '')))
            matched=[a for a in anchors if _contains(text,a)]
            coverage=sum(_contains(text,t) for t in terms)/len(terms) if terms else 0.0
            candidate=(len(matched)==len(anchors),coverage,matched)
            if best is None or candidate[:2]>best[:2]:best=candidate
        intact,coverage,matched=best
        quality.update(lexicalCoverage=coverage,requiredAnchors=anchors,matchedAnchors=matched,queryTermCount=len(terms))
        if not intact:reasons.append('EXACT_CONSTRAINT_MISSING')
        if not terms or coverage<bm25_min_coverage:reasons.append('LEXICAL_COVERAGE_LOW')
    elif route=='DENSE':
        if not scores:reasons.append('DENSE_SCORE_UNAVAILABLE')
        elif scores[0]<dense_min_score:reasons.append('DENSE_SIMILARITY_LOW')
    else:raise ValueError('Quality gate applies only to BM25 or DENSE')
    # A small top-score gap alone is not a failure: duplicates and adjacent
    # correct blocks often tie. BM25 scores are not compared with cosine scores.
    quality.update(accepted=not reasons,reason='LOW_CONFIDENCE' if reasons else 'SUFFICIENT_CONFIDENCE',reasons=reasons)
    return quality


@dataclass(frozen=True)
class FallbackPolicy:
    enabled:bool=True
    max_attempts:int=1
    on_empty:bool=True
    on_low_confidence:bool=True
    def __post_init__(self):
        if self.max_attempts not in (0,1):raise ValueError("Only one supplemental route is permitted")
    def next_channel(self,initial,quality,attempts=0):
        trigger=(self.on_empty and quality.get('reason')=='EMPTY_RESULT' or
                 self.on_low_confidence and quality.get('reason')=='LOW_CONFIDENCE')
        if not self.enabled or attempts>=self.max_attempts or quality.get('accepted',False) or not trigger:return None
        return "DENSE" if initial=="BM25" else "BM25" if initial=="DENSE" else None


def fuse(dense,sparse,k=60,limit=28):
    scores={};values={};ranks={}
    for name,route in (("dense",dense),("bm25",sparse)):
        seen=set();rank=0
        for hit in route:
            key=hit["chunk_id"]
            if key in seen:continue
            seen.add(key);rank+=1;values.setdefault(key,hit);scores[key]=scores.get(key,0)+1/(k+rank)
            ranks.setdefault(key,{})[name+"_rank"]=rank
    order=sorted(values,key=lambda key:(-scores[key],key));selected=set()
    for route in (dense,sparse):selected.update(h["chunk_id"] for h in route[:min(5,limit//2)])
    for key in order:
        if len(selected)>=limit:break
        selected.add(key)
    return [values[key]|ranks[key]|{"rrf_score":scores[key]} for key in order if key in selected]
