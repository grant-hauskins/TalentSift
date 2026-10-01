"""Pydantic models for everything the LLM returns, plus checks the schema alone cannot express.

These classes are sent to the model as a strict JSON schema (see `llm.base.strict_json_schema`) and used
again to validate the reply. Constraints such as "score 0-4" are enforced here even when a provider
ignores them.
"""

from __future__ import annotations

from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- Screening -----------------------------------------------------------------------------------


class CriterionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: int = Field(description="The criterion_id exactly as given in the prompt")
    score: int = Field(ge=0, le=4, description="Integer 0-4. 0 means no evidence found")
    rationale: str = Field(description="1-2 sentences explaining the score, job-related only")
    evidence: list[str] = Field(
        description="Verbatim quotes copied exactly from the resume. Required for scores 1-4; empty for 0"
    )


class ScreeningOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: list[CriterionAssessment] = Field(description="One entry per criterion in the prompt")
    strengths: list[str] = Field(description="Job-related strengths supported by the resume")
    gaps: list[str] = Field(description="Job-related gaps: criteria with little or no evidence")
    summary: str = Field(description="2-3 sentences, job-related only")


def check_screening_output(output: ScreeningOutput, expected_criterion_ids: Iterable[int]) -> None:
    """Semantic checks beyond the schema. Raises ValueError with a message the model can act on."""
    expected = set(expected_criterion_ids)
    returned = [item.criterion_id for item in output.criteria]
    problems: list[str] = []

    missing = sorted(expected - set(returned))
    unexpected = sorted(set(returned) - expected)
    duplicates = sorted({cid for cid in returned if returned.count(cid) > 1})
    if missing:
        problems.append(f"missing criterion_id(s) {missing}")
    if unexpected:
        problems.append(f"unknown criterion_id(s) {unexpected}")
    if duplicates:
        problems.append(f"criterion_id(s) {duplicates} appear more than once")
    for item in output.criteria:
        quotes = [quote for quote in item.evidence if quote.strip()]
        if item.score > 0 and not quotes:
            problems.append(f"criterion_id {item.criterion_id} has score {item.score} but no evidence quote")
    if not output.summary.strip():
        problems.append("summary is empty")
    if problems:
        raise ValueError("; ".join(problems))


# --- Rubric drafting -----------------------------------------------------------------------------


class DraftCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Short label, at most 8 words")
    description: str = Field(description="What resume evidence satisfies this criterion")
    type: Literal["must_have", "nice_to_have"]
    weight: Literal["high", "medium", "low"]
    proxy_risk: bool = Field(
        description="True if this criterion could act as a proxy for a protected characteristic"
    )
    proxy_note: str = Field(description="Why it could be a proxy and how to reword it; empty if proxy_risk is false")


class RubricDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_title: str
    summary: str = Field(description="1-2 sentences describing the role")
    criteria: list[DraftCriterion] = Field(min_length=5, max_length=10, description="Between 5 and 10 criteria")


def check_rubric_draft(draft: RubricDraft) -> None:
    """Semantic checks for a drafted rubric. Raises ValueError."""
    problems: list[str] = []
    names = [criterion.name.strip().lower() for criterion in draft.criteria]
    if any(not name for name in names):
        problems.append("every criterion needs a name")
    if len(set(names)) != len(names):
        problems.append("criterion names must be unique")
    if not any(criterion.type == "must_have" for criterion in draft.criteria):
        problems.append("at least one criterion must be a must_have")
    for criterion in draft.criteria:
        if criterion.proxy_risk and not criterion.proxy_note.strip():
            problems.append(f"criterion '{criterion.name}' has proxy_risk but no proxy_note")
    if problems:
        raise ValueError("; ".join(problems))
