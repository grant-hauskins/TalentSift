"""Improvement suggestions drawn from the score breakdown, with nothing added.

SOW candidate feature: "improvement suggestions drawn from the score breakdown, with no added skills,
credentials, or experience."

Suggestions are written by code, not by an AI, so they cannot invent anything. For each criterion a
version scored low on, the code looks in the candidate's own core facts:

* **include**: a fact supports the criterion, but this version leaves that line out. Suggest using it,
  quoting the candidate's own words.
* **strengthen**: the version already shows the supporting fact, and it still scored low. Suggest moving
  it up or adding real detail to the fact.
* **gap**: nothing in the facts supports the criterion. Say so plainly. The only honest fix is a true new
  core fact; the suggestion never proposes wording for something the candidate has not said.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from talentsift.draft_template import FactView, skill_names
from talentsift.scoring import WEIGHT_POINTS, normalize_for_match
from talentsift.textutil import keywords
from talentsift.version_scoring import CriterionScoreLine, VersionScore, is_unmet, resume_text_for_scoring

LOW_SCORE = 2  # criteria scored 0-2 get a suggestion
MAX_SUGGESTIONS = 6

INCLUDE = "include"
STRENGTHEN = "strengthen"
GAP = "gap"
LABELS = {
    INCLUDE: "In your facts, missing from this version",
    STRENGTHEN: "Already in this version, but thin",
    GAP: "Not in your core facts",
}


@dataclass(frozen=True)
class Suggestion:
    kind: str  # include | strengthen | gap
    criterion: str
    category: str
    score: int  # this version's 0-4 score on the criterion
    must_have: bool
    text: str  # what to do, in plain language
    fact_lines: tuple[str, ...] = ()  # the candidate's own words that support the criterion


def _fact_lines(facts: Sequence[FactView]) -> list[tuple[str, str]]:
    """Every separate statement in the facts, as (text, where it comes from)."""
    lines: list[tuple[str, str]] = [(name, "your skills") for name in skill_names(facts)]
    for fact in facts:
        if fact.category == "skills":
            continue
        source = f"{fact.title}" + (f" at {fact.organization}" if fact.organization else "")
        lines.append((" ".join(part for part in (fact.title, fact.organization) if part), source))
        lines.extend((detail, source) for detail in fact.detail_lines)
    return lines


def _key_terms(criterion) -> set[str]:
    """The criterion's key terms: its name, or name plus description when the name is a single word."""
    terms = set(keywords(criterion.name))
    return terms if len(terms) >= 2 else set(keywords(f"{criterion.name} {criterion.description}"))


def _supports(line: str, wanted: set[str]) -> bool:
    """A fact line supports a criterion when it shares at least half of the criterion's key terms.

    Half, not one: "Data Analyst" must not count as support for "Snowflake data warehouse".
    """
    found = wanted.intersection(keywords(line))
    return bool(found) and len(found) >= math.ceil(len(wanted) / 2)


def _appears(line: str, version_text: str, version_keywords: set[str]) -> bool:
    """True if the version already says this: verbatim, or (after AI rewording) most of its key terms."""
    if normalize_for_match(line) in version_text:
        return True
    terms = set(keywords(line))
    return bool(terms) and len(terms & version_keywords) / len(terms) >= 0.75


def suggest(score: VersionScore, facts: Sequence[FactView]) -> list[Suggestion]:
    """Suggestions for one scored version, most valuable first. Empty if it was not scored."""
    if score.fit is None:
        return []
    body = resume_text_for_scoring(score.version.body)
    version_text, version_keywords = normalize_for_match(body), set(keywords(body))
    fact_lines = _fact_lines(facts)

    def priority(line: CriterionScoreLine) -> tuple:
        return (not is_unmet(line), line.score, -WEIGHT_POINTS[line.criterion.weight], line.criterion.id)

    suggestions = []
    for line in sorted((l for l in score.lines if l.score <= LOW_SCORE), key=priority):
        criterion = line.criterion
        wanted = _key_terms(criterion)
        supporting = [(text, source) for text, source in fact_lines if _supports(text, wanted)]
        missing = [(text, source) for text, source in supporting if not _appears(text, version_text, version_keywords)]
        common = dict(criterion=criterion.name, category=criterion.category, score=line.score, must_have=criterion.type == "must_have")
        if missing:
            quoted = tuple(f"{text} ({source})" for text, source in missing[:3])
            suggestions.append(Suggestion(
                INCLUDE, text="Your core facts support this, but this version leaves it out. Draft again for this role, or shorten "
                "other facts so these lines make it in.", fact_lines=quoted, **common))
        elif supporting:
            quoted = tuple(f"{text} ({source})" for text, source in supporting[:3])
            suggestions.append(Suggestion(
                STRENGTHEN, text="This version already shows it, and it still scored low. If there is more that is true (a tool you "
                "used, a result, how often), add that detail to the core fact and draft again.", fact_lines=quoted, **common))
        else:
            suggestions.append(Suggestion(
                GAP, text="Nothing in your core facts shows this. If you have it, add it as a core fact with where and when. "
                "If you do not, leave it out: TalentSift will not write it for you.", **common))
    return suggestions[:MAX_SUGGESTIONS]
