"""Conservative, query-only clause expansion. Not enabled in production.

Splits explicit interrogative clauses, not arbitrary noun phrases. Every emitted
token comes from the original question. No synonym, answer or corpus access.
"""
import re

VERSION = 'explicit-question-clauses-v1'
EN_CUE = r'(?:what|which|when|where|why|how|who|under\s+what|in\s+how|to\s+what)\b'
ZH_CUE = r'什么|多少|哪些|如何|何时|是否|怎么|多大|几(?:个|年|月|家|人|项)|怎样'

def split_query(question):
    if not isinstance(question, str) or not question.strip() or len(question)>1500:
        return dict(version=VERSION, queries=[], reason='INVALID_OR_TOO_LONG')
    text=question.strip()
    # AND in names, units, lists and quoted text must not be split blindly.
    if re.search(r'["“”「」`]',text):
        return dict(version=VERSION, queries=[], reason='QUOTED_QUERY')
    zh=bool(re.search(r'[\u3400-\u9fff]',text))
    if zh:
        boundary=re.compile(r'[，；;]\s*|(?:并且|以及|同时|而且)')
        matches=[m for m in boundary.finditer(text)
                 if re.search(ZH_CUE,text[m.end():]) and re.search(ZH_CUE,text[:m.start()])]
    else:
        boundary=re.compile(r'(?:,?\s+and\s+|;\s*|\?\s+)(?='+EN_CUE+')',re.I)
        matches=[m for m in boundary.finditer(text) if re.search(EN_CUE,text[:m.start()],re.I)]
    if len(matches)!=1:
        return dict(version=VERSION, queries=[], reason='NO_SINGLE_EXPLICIT_BOUNDARY')
    boundary=matches[0]
    left=text[:boundary.start()].strip(' ,;，；?？')
    right=text[boundary.end():].strip(' ,;，；?？')
    if min(len(left),len(right))<5:
        return dict(version=VERSION, queries=[], reason='SHORT_OR_AMBIGUOUS_CLAUSE')
    # Global numeric constraints and identifiers/names are kept on both branches.
    anchors=[]
    patterns=[r'\b[A-Z][A-Za-z0-9]*(?:[-./][A-Za-z0-9]+)*\b',r'\d+(?:[.,]\d+)*(?:%|年)?']
    for pattern in patterns:
        for m in re.finditer(pattern,text):
            token=m.group()
            if token.lower() not in {'what','which','when','where','why','how','who','according','in','the'} and token not in anchors:
                anchors.append(token)
    if zh:
        # Repeat a short leading subject when it precedes a question cue. If its
        # boundary is unclear, retain it as extra query context rather than invent.
        first=re.search(ZH_CUE,left)
        if first:
            prefix=left[:first.start()].strip()
            if 2<=len(prefix)<=48:anchors.insert(0,prefix)
    queries=[]
    for clause in (left,right):
        missing=[a for a in anchors if a.casefold() not in clause.casefold()]
        q=(' '.join(missing)+' '+clause).strip()
        if q!=text and q not in queries:queries.append(q)
    if len(queries)!=2:
        return dict(version=VERSION, queries=[], reason='NO_DISTINCT_PAIR')
    return dict(version=VERSION,queries=queries,reason='EXPLICIT_QUESTION_CLAUSES',
                clauses=[left,right],anchors=anchors,original_query=text)
