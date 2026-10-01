"""Deterministic offline LLM client for tests and as a demo backup.

It reads the same prompt a real model receives and answers with explainable keyword matching:

* Screening: each criterion's keywords (from its name and description) are looked up line by line in the
  masked resume. Coverage of those keywords sets the 0-4 score, and the best matching lines become verbatim
  evidence quotes, so they always verify.
* Rubric drafting: bullet points under "Requirements"-style headings become must-haves, bullets under
  "Preferred"-style headings become nice-to-haves, and known proxy phrases are flagged.

For tests it can also replay scripted replies (`script`) or alter its own replies (`tamper`).
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Callable

from pydantic import BaseModel

from talentsift.llm.base import LLMError, LLMResult, extract_json_object
from talentsift.schemas import RubricDraft, ScreeningOutput

FAKE_MODEL = "fake/keyword-matcher-v1"

# --- Text helpers ----------------------------------------------------------------------------------

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


def _tag(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}[^>]*>\n?(.*?)\n?</{tag}>", text, re.DOTALL)
    return match.group(1) if match else ""


# --- Offline screening -----------------------------------------------------------------------------


def _score_from_coverage(coverage: float) -> int:
    if coverage <= 0:
        return 0
    if coverage >= 0.75:
        return 4
    if coverage >= 0.5:
        return 3
    if coverage >= 0.3:
        return 2
    return 1


def screen_offline(user_prompt: str) -> dict[str, Any]:
    """Score the resume in a screening prompt against its criteria (see prompts/screen_v1.md)."""
    criteria = json.loads(_tag(user_prompt, "criteria") or "[]")
    resume = _tag(user_prompt, "resume")
    # Lines without bullet markers, so quotes read cleanly (they remain verbatim substrings of the resume).
    lines = [re.sub(r"^[-*•]\s+", "", line.strip()) for line in resume.splitlines() if line.strip()]
    line_keywords = [set(keywords(line)) for line in lines]

    assessments, strengths, gaps = [], [], []
    for criterion in criteria:
        wanted = keywords(f"{criterion['name']} {criterion.get('description', '')}")
        hits_per_line = [len(set(wanted) & found) for found in line_keywords]
        found = set().union(*(set(wanted) & found for found in line_keywords)) if lines else set()
        coverage = len(found) / len(wanted) if wanted else 0.0
        score = _score_from_coverage(coverage)
        best_lines = sorted((i for i, hits in enumerate(hits_per_line) if hits), key=lambda i: (-hits_per_line[i], i))
        evidence = [lines[i] for i in best_lines[:2]] if score else []
        if score == 0:
            rationale = "No evidence found."
        else:
            matched = ", ".join(word for word in wanted if word in found)
            rationale = f"The resume mentions {matched} ({len(found)} of {len(wanted)} key terms for this criterion)."
        assessments.append(
            {"criterion_id": criterion["criterion_id"], "score": score, "rationale": rationale, "evidence": evidence}
        )
        if score >= 3:
            strengths.append(f"Clear evidence for {criterion['name']}.")
        elif score <= 1:
            gaps.append(f"{'No' if score == 0 else 'Little'} evidence found for {criterion['name']}.")

    if strengths:
        summary = f"The applicant shows clear evidence for {len(strengths)} of {len(criteria)} criteria."
    else:
        summary = f"The applicant shows limited evidence for the {len(criteria)} criteria in this rubric."
    if gaps:
        summary += f" The resume shows little or no evidence for {len(gaps)} criteria."
    return {"criteria": assessments, "strengths": strengths, "gaps": gaps, "summary": summary}


# --- Offline rubric drafting ------------------------------------------------------------------------

_MUST_HEADING = re.compile(
    r"^(requirements?|required( qualifications| skills)?|must[- ]haves?|minimum qualifications|"
    r"qualifications|what you('ll)? need|what we need|you have)\b",
    re.IGNORECASE,
)
_NICE_HEADING = re.compile(
    r"^(preferred( qualifications| skills)?|nice[- ]to[- ]haves?|bonus( points)?|pluses|desired|"
    r"it'?s a plus)\b",
    re.IGNORECASE,
)
_OTHER_HEADING = re.compile(
    r"^(responsibilities|what you('ll)? do|duties|about( us| the role)?|benefits|perks|compensation|"
    r"how to apply|location|schedule|overview)\b",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^\s*(?:[-*•▪◦]|\d+[.)])\s+(.*\S)\s*$")
_LEADING_FILLER = re.compile(
    r"^(\d+\+?\s*(years?|yrs?)\s*(of)?\s*|experience (with|in|using)\s+|proficiency (with|in)\s+|"
    r"familiarity with\s+|knowledge of\s+|ability to\s+|strong\s+|solid\s+|demonstrated\s+|"
    r"hands-on\s+|comfortable\s+(with\s+)?|working knowledge of\s+)+",
    re.IGNORECASE,
)

PROXY_RULES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"\b(recent (college |university )?grad(uate)?s?|graduated (with)?in|digital natives?|young|"
                   r"youthful|energetic|years old)\b", re.I),
        "May act as a proxy for age. Describe the skills or knowledge the job needs instead.",
    ),
    (
        re.compile(r"\b(native (english )?speakers?|mother tongue|no accent|accent[- ]free)\b", re.I),
        "May act as a proxy for national origin. State the language proficiency the job actually requires.",
    ),
    (
        re.compile(r"\b(no (employment |work )?gaps?|continuous employment|gaps? in employment)\b", re.I),
        "Employment gaps often reflect caregiving, illness, or military service (family status, disability). "
        "Judge current skills instead.",
    ),
    (
        re.compile(r"\b(culture fit|fits? (in(to)? )?our culture|like-minded)\b", re.I),
        "Vague 'fit' criteria can screen people out by background. Name the specific behaviors the job needs.",
    ),
    (
        re.compile(r"\b(top[- ]tier|ivy league|prestigious|elite (school|university|college)s?)\b", re.I),
        "School prestige tracks socioeconomic background and race more than ability. Judge skills and results.",
    ),
    (
        re.compile(r"\b(able-bodied|physically fit|no disabilit(y|ies)|u\.?s\.?[- ]born|married|"
                   r"single|childless|no children)\b", re.I),
        "Refers to a protected characteristic (disability, national origin, or family status). Remove it.",
    ),
]


def proxy_note(text: str) -> str:
    for pattern, note in PROXY_RULES:
        if pattern.search(text):
            return note
    return ""


_CONNECTORS = re.compile(r"\s+(?:to|for|with|such as|including|across|in order to)\s+", re.IGNORECASE)


def _criterion_name(bullet: str) -> str:
    """A short label from a bullet: drop filler ("2+ years of"), cut at a connector ("to", "for", ...)."""
    text = _LEADING_FILLER.sub("", bullet).strip()
    text = re.split(r"[.;:(]", text)[0].strip() or bullet
    head = _CONNECTORS.split(text, maxsplit=1)[0]
    if len(head.split()) >= 2:
        text = head
    name = " ".join(text.split()[:7])
    return name[:1].upper() + name[1:]


def draft_rubric_offline(user_prompt: str) -> dict[str, Any]:
    """Draft a rubric from the posting in a rubric prompt (see prompts/rubric_draft_v1.md)."""
    posting = _tag(user_prompt, "job_posting")
    title_line = re.search(r"Role title from the manager \(may be empty\):[ \t]*(.*)", user_prompt)
    title = (title_line.group(1).strip() if title_line else "") or next(
        (line.strip() for line in posting.splitlines() if line.strip()), "Untitled role"
    )

    must, nice, other, intro = [], [], [], []
    section = None
    for raw in posting.splitlines():
        line = raw.strip()
        if not line:
            continue
        bullet = _BULLET.match(line)
        heading = line.rstrip(":").strip()
        if not bullet and len(heading) < 60:
            if _MUST_HEADING.match(heading):
                section = "must"
                continue
            if _NICE_HEADING.match(heading):
                section = "nice"
                continue
            if _OTHER_HEADING.match(heading):
                section = "other"
                continue
        if bullet:
            {"must": must, "nice": nice}.get(section, other).append(bullet.group(1))
        elif section is None and line != title:
            intro.append(line)

    picked = [(text, "must_have") for text in must] + [(text, "nice_to_have") for text in nice]
    for text in other:  # pad short postings with responsibilities, as nice-to-haves
        if len(picked) >= 5:
            break
        picked.append((text, "nice_to_have"))
    picked = picked[:10]

    criteria, seen_names = [], set()
    nice_count = 0
    for text, kind in picked:
        name = _criterion_name(text)
        if name.lower() in seen_names:
            continue
        seen_names.add(name.lower())
        if kind == "must_have":
            weight = "high"
        else:
            weight = "medium" if nice_count < 2 else "low"
            nice_count += 1
        note = proxy_note(text)
        criteria.append(
            {
                "name": name,
                "description": text,
                "type": kind,
                "weight": weight,
                "proxy_risk": bool(note),
                "proxy_note": note,
            }
        )
    if criteria and not any(c["type"] == "must_have" for c in criteria):
        criteria[0]["type"], criteria[0]["weight"] = "must_have", "high"

    summary = intro[0] if intro else f"Screening rubric drafted from the {title} posting."
    return {"role_title": title, "summary": summary[:300], "criteria": criteria}


# --- The client ------------------------------------------------------------------------------------


class FakeLLMClient:
    """Implements `LLMClient` without any network access."""

    provider = "fake"

    def __init__(
        self,
        model: str = FAKE_MODEL,
        *,
        script: list[str | dict | Exception] | None = None,
        tamper: Callable[[dict, str], dict | str] | None = None,
        cost_per_call: float = 0.0,
    ):
        self.model = model
        self._script = list(script or [])
        self._tamper = tamper  # tests: change a reply, e.g. inject a fabricated quote
        self.cost_per_call = cost_per_call
        self.calls: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def complete_json(self, system: str, user: str, schema: type[BaseModel]) -> LLMResult:
        with self._lock:
            self.calls.append({"system": system, "user": user, "schema": schema.__name__})
            scripted = self._script.pop(0) if self._script else None

        if isinstance(scripted, Exception):
            raise scripted
        if scripted is not None:
            raw = scripted if isinstance(scripted, str) else json.dumps(scripted)
        else:
            if schema is ScreeningOutput:
                reply: dict | str = screen_offline(user)
            elif schema is RubricDraft:
                reply = draft_rubric_offline(user)
            else:
                raise LLMError(f"The fake client has no offline behavior for {schema.__name__}")
            if self._tamper is not None:
                reply = self._tamper(reply, user)
            raw = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)

        prompt_tokens = (len(system) + len(user)) // 4
        completion_tokens = len(raw) // 4
        return LLMResult(
            raw_text=raw,
            parsed=extract_json_object(raw),
            model_requested=self.model,
            model_used=self.model,
            provider_used="offline",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=self.cost_per_call,
            request={"model": self.model, "temperature": 0.0, "schema": schema.__name__},
        )
