"""Bounded, one-hop lexical aliases. No network, model or document writes."""
import hashlib
import json
import re
import unicodedata
from pathlib import Path


def normalize(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def contains(text, term):
    pattern = re.escape(term)
    if term[0].isascii() and term[0].isalnum():
        pattern = r"(?<![a-z0-9_])" + pattern
    if term[-1].isascii() and term[-1].isalnum():
        pattern += r"(?![a-z0-9_])"
    return re.search(pattern, normalize(text)) is not None


class QueryExpander:
    def __init__(self, path, *, enabled=False, weight=0.25, max_terms=6):
        if not 0 < weight <= 0.5 or not 1 <= max_terms <= 12:
            raise ValueError("Invalid synonym weight or expansion limit")
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        groups = payload["groups"]
        if not isinstance(groups, list) or len(groups) > 256:
            raise ValueError("Synonym dictionary exceeds group limit")
        self.groups = []
        seen = set()
        for group in groups:
            if not isinstance(group, list) or not 2 <= len(group) <= 8:
                raise ValueError("Each synonym group requires 2..8 terms")
            terms = []
            for raw in group:
                if not isinstance(raw, str):raise ValueError("Synonym must be text")
                term = normalize(raw)
                if not 2 <= len(term) <= 64 or any(c.isdigit() for c in term):
                    raise ValueError("Synonyms must be bounded concepts, without numeric facts")
                if term in seen:raise ValueError("Duplicate/overlapping synonym groups")
                seen.add(term);terms.append(term)
            self.groups.append(terms)
        self.enabled, self.weight, self.max_terms = enabled, weight, max_terms
        canonical = json.dumps(self.groups, ensure_ascii=False, separators=(",", ":"))
        self.version = hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def expand(self, question):
        additions, matches = [], []
        if self.enabled:
            for group in self.groups:
                found = [term for term in group if contains(question, term)]
                if not found:continue
                selected = [term for term in group if term not in found][:self.max_terms-len(additions)]
                additions.extend(selected)
                if selected:matches.append({"matched": found, "added": selected})
                if len(additions) >= self.max_terms:break
        return {"policy_version": "query-alias-v1", "enabled": self.enabled, "dictionary_version": self.version,
                "original_query": question, "terms": additions, "matches": matches,
                "expanded_query": question + (" " + " ".join(additions) if additions else ""),
                "route_weight": self.weight, "max_terms": self.max_terms, "llmCalls": 0}

    def sentence_bonus(self, question, sentence):
        # Count concepts, not aliases: a long group cannot accumulate many votes.
        if not self.enabled:return 0.0
        plan = self.expand(question)
        return self.weight * sum(any(contains(sentence, term) for term in match["added"])
                                 for match in plan["matches"])
