"""Small text helpers shared by the offline client and the no-new-facts guard.

`keywords` turns a sentence into its distinct, lightly stemmed content words. It is deliberately simple
and deterministic: the same text always gives the same list.
"""

from __future__ import annotations

import re

_STOPWORDS = {
    "a", "an", "and", "or", "the", "of", "for", "to", "in", "on", "at", "by", "with", "from", "into", "as",
    "is", "are", "be", "been", "being", "that", "this", "these", "those", "it", "its", "our", "your", "you",
    "we", "they", "their", "who", "what", "which", "will", "can", "may", "must", "should", "would", "such",
    "other", "than", "then", "per", "via", "etc", "e.g", "i.e", "and/or", "not", "no", "all", "any", "each",
    "both", "more", "most", "least", "about", "over", "under", "up", "out", "also", "has", "have", "had",
    "non", "my", "me", "i",
}  # fmt: skip
# Words that appear in almost every posting and say little about a specific skill.
_GENERIC = {
    "experience", "experienced", "ability", "able", "skill", "skills", "skilled", "strong", "work", "working",
    "year", "years", "knowledge", "using", "use", "including", "related", "proficiency", "proficient",
    "familiarity", "familiar", "excellent", "good", "great", "demonstrated", "plus", "preferred", "required",
    "requirement", "candidate", "role", "team", "teams", "tool", "tools", "level", "based", "minimum",
    "comfortable", "solid", "hands-on", "evidence", "resume", "applicant", "ideal", "background", "similar",
}  # fmt: skip

_TOKEN = re.compile(r"[a-z][a-z0-9+#]*(?:/[a-z0-9]+)?")


def _stem(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:  # queries -> query
        return word[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    return word


def keywords(text: str) -> list[str]:
    """Distinct content words of a text, lightly stemmed, in order of first appearance."""
    seen: dict[str, None] = {}
    for token in _TOKEN.findall(text.lower()):
        if len(token) < 2 or token in _STOPWORDS or token in _GENERIC:
            continue
        stem = _stem(token)
        if stem not in _STOPWORDS and stem not in _GENERIC:
            seen.setdefault(stem, None)
    return list(seen)
