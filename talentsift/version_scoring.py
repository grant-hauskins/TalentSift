"""A/B scoring: score a candidate's saved resume versions against one job description.

SOW candidate feature: "A/B testing of resume versions against a role to see which scores best."

The candidate side has no access to employer data, so nothing here reads or writes roles, applicants, or
screening runs. Instead it reuses the same engine on the candidate's own material:

1. The pasted job description becomes criteria with the rubric prompt (once, shared by every version, so
   the comparison is like for like). Criteria flagged as possible proxies are dropped.
2. Each version is scored with the screening prompt: 0-4 per criterion with verbatim evidence, checked
   against the version's text. The name and contact header is removed first.
3. The fit score is computed in code by `scoring.compute_fit_score`, with equal category weights.

The result is an estimate. A real employer chooses their own criteria and weights, so a higher score here
means "matches this posting's wording and requirements better", not "will be shortlisted".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from sqlmodel import Session

from talentsift.audit import log_event, sha256_text
from talentsift.candidates import CandidateError
from talentsift.config import Settings
from talentsift.llm.base import LLMClient, call_with_validation
from talentsift.models import CATEGORIES, DEFAULT_CATEGORY_WEIGHTS, CandidateProfile, ResumeVersion
from talentsift.prompts import SCREEN_PROMPT, load_prompt
from talentsift.rubric import draft_criteria
from talentsift.schemas import ScreeningOutput, check_screening_output
from talentsift.scoring import (
    UNMET_MUST_HAVE_MAX_SCORE,
    CriterionResult,
    FitResult,
    compute_fit_score,
    criteria_prompt_json,
    normalize_for_match,
    verify_quote,
)

MAX_VERSIONS = 4


@dataclass(frozen=True)
class ScoreCriterion:
    """One criterion drafted from the candidate's pasted job description (never stored as a role)."""

    id: int
    name: str
    description: str
    type: str  # must_have | nice_to_have
    weight: str  # high | medium | low
    category: str


@dataclass
class CriterionScoreLine:
    criterion: ScoreCriterion
    score: int  # 0-4
    rationale: str
    evidence: list[str]
    verified: bool  # every evidence quote was found in the version's text


@dataclass
class VersionScore:
    version: ResumeVersion
    fit: FitResult | None = None
    lines: list[CriterionScoreLine] = field(default_factory=list)
    error: str = ""  # why this version could not be scored

    @property
    def fit_score(self) -> float | None:
        return self.fit.fit_score if self.fit else None

    @property
    def unverified(self) -> int:
        return sum(1 for line in self.lines if not line.verified)


@dataclass
class ABResult:
    role_title: str
    role_text: str
    criteria: list[ScoreCriterion]
    scores: list[VersionScore]
    model: str
    provider: str

    @property
    def scored(self) -> list[VersionScore]:
        return [s for s in self.scores if s.fit is not None]

    @property
    def best(self) -> list[VersionScore]:
        """The top-scoring version, or several when they tie. Empty if nothing was scored."""
        scored = self.scored
        if not scored:
            return []
        top = max(s.fit_score for s in scored)
        return [s for s in scored if s.fit_score == top]


def resume_text_for_scoring(body: str) -> str:
    """The version's text without the name and contact header (everything before the first section)."""
    lines = body.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("## ")), 0)
    text = "\n".join(lines[start:])
    return text.replace("**", "").replace("## ", "").strip()


def _score_one(version: ResumeVersion, criteria: Sequence[ScoreCriterion], role_title: str, client: LLMClient, settings: Settings) -> VersionScore:
    prompt = load_prompt(SCREEN_PROMPT, settings.prompts_dir)
    text = resume_text_for_scoring(version.body)[: settings.max_resume_chars]
    expected = [c.id for c in criteria]
    user = prompt.render_user(
        role_title=role_title or "(not given)",
        role_summary="(none)",
        criteria_json=criteria_prompt_json(criteria),
        applicant_label="Candidate version",
        resume_text=text,
    )
    call = call_with_validation(client, prompt.system, user, ScreeningOutput, check=lambda out: check_screening_output(out, expected))
    if not call.ok:
        return VersionScore(version, error=call.fatal_error or call.last_error or "the AI gave no usable answer")

    normalized = normalize_for_match(text)
    by_id = {item.criterion_id: item for item in call.value.criteria}
    lines = []
    for criterion in criteria:
        item = by_id[criterion.id]
        quotes = [q.strip() for q in item.evidence if q.strip()]
        lines.append(
            CriterionScoreLine(criterion, item.score, item.rationale.strip(), quotes, all(verify_quote(q, normalized) for q in quotes))
        )
    fit = compute_fit_score(
        [CriterionResult(line.criterion.id, line.criterion.type, line.criterion.weight, line.score, line.criterion.category) for line in lines],
        settings.must_have_penalty,
        DEFAULT_CATEGORY_WEIGHTS,
    )
    return VersionScore(version, fit=fit, lines=lines)


def score_versions(
    session: Session,
    profile: CandidateProfile,
    versions: Sequence[ResumeVersion],
    client: LLMClient,
    settings: Settings,
    *,
    role_title: str = "",
    role_text: str,
) -> ABResult:
    """Score the candidate's chosen versions against one job description, with one shared set of criteria."""
    versions = list(versions)
    if not versions:
        raise CandidateError("Choose at least one saved version to score.")
    if len(versions) > MAX_VERSIONS:
        raise CandidateError(f"Score up to {MAX_VERSIONS} versions at a time.")
    if any(v.profile_id != profile.id for v in versions):
        raise CandidateError("You can only score your own versions.")
    drafted, error = draft_criteria(client, settings, posting_text=role_text, title=role_title)
    if error:
        raise CandidateError(error)
    if not drafted:
        raise CandidateError("No usable requirements were found in that job description.")
    criteria = [
        ScoreCriterion(index, c.name, c.description, c.type, c.weight, c.category if c.category in CATEGORIES else "skills")
        for index, c in enumerate(drafted, start=1)
    ]
    scores = [_score_one(version, criteria, role_title.strip(), client, settings) for version in versions]

    # Ids, counts, and hashes only: the candidate's text and the posting never go in the audit log.
    log_event(
        session,
        "versions_scored",
        model=client.model,
        provider=client.provider,
        prompt_version=SCREEN_PROMPT,
        input_hash=sha256_text(role_text),
        payload={
            "profile_id": profile.id,
            "version_ids": [v.id for v in versions],
            "criteria_count": len(criteria),
            "fit_scores": [s.fit_score for s in scores],
            "failed": sum(1 for s in scores if s.fit is None),
            "unverified_evidence": sum(s.unverified for s in scores),
        },
    )
    session.commit()
    return ABResult(role_title.strip(), role_text, criteria, scores, client.model, client.provider)


def is_unmet(line: CriterionScoreLine) -> bool:
    return line.criterion.type == "must_have" and line.score <= UNMET_MUST_HAVE_MAX_SCORE
