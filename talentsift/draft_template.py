"""Build a resume draft from core facts alone, with no AI: the safe fallback for the drafting assistant.

Every word of the output comes from the candidate's facts or from a handful of fixed connecting words, so
the result always passes the no-new-facts guard. It is used three ways:

* as the fallback when an AI draft fails the guard twice (docs/drafting_guard.md),
* as the offline client's "model" for tests and demos without API spend,
* as the reference for what a guard-clean draft looks like.

This module has no database or LLM imports so both `drafting.py` and the offline client can use it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from talentsift.schemas import DraftExperience, ResumeDraft
from talentsift.textutil import keywords

MAX_BULLETS = 6
MAX_SUMMARY_SKILLS = 5
_BULLET_MARK = re.compile(r"^\s*(?:[-*•▪◦]|\d+[.)])\s+")
_SKILL_SPLIT = re.compile(r"\s*[,;\n]\s*")


@dataclass(frozen=True)
class FactView:
    """A core fact as plain data (the guard and the template never touch the database)."""

    id: int
    category: str  # skills | education | certifications | experience
    title: str
    organization: str = ""
    start: str = ""
    end: str = ""
    details: str = ""

    @property
    def detail_lines(self) -> list[str]:
        return [_BULLET_MARK.sub("", line).strip() for line in self.details.splitlines() if line.strip()]

    @property
    def text(self) -> str:
        """Everything the candidate wrote for this fact, as one searchable string."""
        return "\n".join(part for part in (self.title, self.organization, self.start, self.end, self.details) if part)

    def as_prompt_dict(self) -> dict:
        return {
            "fact_id": self.id,
            "category": self.category,
            "title": self.title,
            "organization": self.organization,
            "start": self.start,
            "end": self.end,
            "details": self.detail_lines,
        }


def skill_names(facts: Sequence[FactView]) -> list[str]:
    """Individual skills from the skill facts ("SQL, Tableau" is two skills), in the candidate's order."""
    seen: dict[str, str] = {}
    for fact in facts:
        if fact.category != "skills":
            continue
        for name in _SKILL_SPLIT.split(fact.title):
            name = name.strip()
            if name:
                seen.setdefault(name.casefold(), name)
    return list(seen.values())


def _relevance(text: str, role_keywords: set[str]) -> int:
    return len(role_keywords.intersection(keywords(text)))


def template_draft(facts: Sequence[FactView], role_text: str = "", headline: str = "") -> ResumeDraft:
    """Arrange the candidate's own words for a target role: most relevant skills and bullets first."""
    role_keywords = set(keywords(role_text))

    def by_relevance(items: list, text_of) -> list:
        # Stable sort: ties keep the candidate's order, so the same input always gives the same draft.
        return sorted(items, key=lambda item: -_relevance(text_of(item), role_keywords))

    skills = by_relevance(skill_names(facts), lambda name: name)
    jobs = by_relevance([f for f in facts if f.category == "experience"], lambda f: f.text)

    experience = []
    for job in jobs:
        bullets = by_relevance(job.detail_lines, lambda line: line)[:MAX_BULLETS]
        if not bullets:  # a job with no details still appears, described by its own title
            bullets = [f"{job.title} at {job.organization}." if job.organization else f"{job.title}."]
        experience.append(DraftExperience(fact_id=job.id, bullets=bullets))

    sentences = []
    if headline.strip():
        sentences.append(headline.strip().rstrip(".") + ".")
    if jobs:
        first = jobs[0]
        where = f" at {first.organization}" if first.organization else ""
        sentences.append(f"Experience as {first.title}{where}.")
    if skills:
        sentences.append("Skills include " + ", ".join(skills[:MAX_SUMMARY_SKILLS]) + ".")

    return ResumeDraft(
        summary=" ".join(sentences),
        skills=skills,
        experience=experience,
        education_ids=[f.id for f in facts if f.category == "education"],
        certification_ids=[f.id for f in facts if f.category == "certifications"],
    )
