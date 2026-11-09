"""Database tables (SQLModel).

Conventions
-----------
* Timestamps are UTC (`utcnow()`) and shown as UTC in the UI.
* Lists and dicts are stored as JSON text in `*_json` columns and decoded with `loads()`.
* `AuditEvent` and `Override` are append-only. `db.init_db` installs SQLite triggers that reject
  UPDATE and DELETE on those tables, and `db.py` also blocks ORM edits before they reach SQL.

Fields beyond the original brief are marked "(ext)" and explained in docs/decisions.md.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Current time in UTC (timezone-aware; SQLModel stores and returns UTC)."""
    return datetime.now(timezone.utc)


def dumps(value: Any) -> str:
    """Stable JSON encoding for `*_json` columns."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def loads(text: str | None, default: Any = None) -> Any:
    """Decode a `*_json` column, returning `default` for empty values."""
    if not text:
        return default
    return json.loads(text)


# --- Allowed values ------------------------------------------------------------------------------

CRITERION_TYPES = ("must_have", "nice_to_have")
CRITERION_WEIGHTS = ("high", "medium", "low")
CRITERION_SOURCES = ("ai_draft", "manager")

# Scoring categories (SOW section 6): the employer weights these four against each other, and a
# candidate's core facts are filed under the same four.
CATEGORIES = ("skills", "education", "certifications", "experience")
DEFAULT_CATEGORY = "skills"
CATEGORY_LABELS = {
    "skills": "Skills",
    "education": "Education",
    "certifications": "Certifications",
    "experience": "Experience",
}
DEFAULT_CATEGORY_WEIGHTS = {category: 25 for category in CATEGORIES}

# Parse statuses. Only "parsed" and "partial" resumes are screened; "partial" never auto-rejects.
PARSE_PARSED = "parsed"
PARSE_PARTIAL = "partial"  # some pages had no extractable text (e.g. a scanned page)
PARSE_NEEDS_OCR = "needs_ocr"  # almost no text at all; skipped with a clear message
PARSE_ERROR = "error"  # the file could not be read
PARSE_UNSUPPORTED = "unsupported"  # no parser for this format yet
SCREENABLE_PARSE_STATUSES = (PARSE_PARSED, PARSE_PARTIAL)

# Evaluation statuses.
STATUS_SHORTLISTED = "shortlisted"
STATUS_NOT_SHORTLISTED = "not_shortlisted"
STATUS_AUTO_REJECTED = "auto_rejected"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_PENDING_AUTO_REJECT = "pending_auto_reject"  # (ext) confirm mode: awaiting manager approval
EVALUATION_STATUSES = (
    STATUS_SHORTLISTED,
    STATUS_NOT_SHORTLISTED,
    STATUS_AUTO_REJECTED,
    STATUS_NEEDS_REVIEW,
    STATUS_PENDING_AUTO_REJECT,
)

# Screening run statuses.
RUN_RUNNING = "running"
RUN_COMPLETED = "completed"
RUN_STOPPED_COST_CAP = "stopped_cost_cap"
RUN_FAILED = "failed"


# --- Tables --------------------------------------------------------------------------------------


class Role(SQLModel, table=True):
    """A job the manager is hiring for. The rubric is versioned; approved versions never change."""

    __tablename__ = "role"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    summary: str = ""
    posting_text: str = ""
    version: int = 1  # current rubric version (may be a draft)
    approved_version: Optional[int] = None  # (ext) last approved version; screening needs version == approved_version
    approved_at: Optional[datetime] = None  # (ext)
    auto_reject_threshold: int = 40  # fit score 0-100
    top_n: int = 5
    # Employer weights per category for the current rubric version, e.g. {"skills": 40, ...}.
    # Part of the rubric: changing them on an approved version creates a new draft version.
    category_weights_json: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def is_approved(self) -> bool:
        return self.approved_version == self.version

    @property
    def category_weights(self) -> dict[str, int]:
        """Weights for all four categories (equal when the manager has not set any)."""
        stored = loads(self.category_weights_json, default={}) or {}
        return {c: int(stored.get(c, DEFAULT_CATEGORY_WEIGHTS[c])) for c in CATEGORIES}


class Criterion(SQLModel, table=True):
    """One rubric line. Rows belong to a single rubric version so old runs stay explainable."""

    __tablename__ = "criterion"

    id: Optional[int] = Field(default=None, primary_key=True)
    role_id: int = Field(foreign_key="role.id", index=True)
    role_version: int = 1  # (ext) the rubric version this row belongs to
    name: str
    description: str = ""
    type: str = "nice_to_have"  # must_have | nice_to_have
    weight: str = "medium"  # high | medium | low
    category: str = DEFAULT_CATEGORY  # skills | education | certifications | experience
    order: int = 0
    source: str = "manager"  # ai_draft | manager
    proxy_warning: str = ""  # (ext) AI note when a criterion could proxy for a protected trait


class Applicant(SQLModel, table=True):
    """One ingested resume. The LLM only ever sees `masked_text` and `display_label`."""

    __tablename__ = "applicant"

    id: Optional[int] = Field(default=None, primary_key=True)
    display_label: str = ""  # e.g. "Applicant 07"; assigned from the id after insert
    original_filename: str
    file_hash: str = Field(index=True, unique=True)  # sha256 of the file bytes; blocks duplicates
    batch: str = Field(default="default", index=True)  # (ext) upload or folder import label
    parse_status: str = PARSE_PARSED
    parse_message: str = ""  # (ext) human-readable parse note (e.g. why it needs OCR)
    page_count: int = 0  # (ext)
    raw_text: str = ""
    masked_text: str = ""
    mask_counts_json: str = "{}"  # (ext) how many items of each PII kind were masked
    created_at: datetime = Field(default_factory=utcnow)


class ScreeningRun(SQLModel, table=True):
    """One click of "Run screening": a role version scored against a set of applicants."""

    __tablename__ = "screening_run"

    id: Optional[int] = Field(default=None, primary_key=True)
    role_id: int = Field(foreign_key="role.id", index=True)
    role_version: int
    model_requested: str
    llm_provider: str = ""  # (ext) "openrouter" or "fake"
    prompt_version: str
    batch_label: str = ""  # (ext) which batches were screened
    # Policy snapshot (ext): the values in force when this run made its decisions.
    auto_reject_threshold: int = 40
    top_n: int = 5
    auto_reject_mode: str = "automatic"
    must_have_penalty: float = 15.0
    category_weights_json: str = ""  # the employer's category weights used by this run
    cost_cap_usd: float = 0.0
    force_rescore: bool = False
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None
    status: str = RUN_RUNNING
    status_message: str = ""  # (ext)
    total_tokens: int = 0
    total_cost: float = 0.0
    applicants_total: int = 0  # (ext)
    applicants_scored: int = 0  # (ext)
    llm_calls: int = 0  # (ext)
    cache_hits: int = 0  # (ext)


class Evaluation(SQLModel, table=True):
    """The result for one applicant in one run."""

    __tablename__ = "evaluation"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="screening_run.id", index=True)
    applicant_id: int = Field(foreign_key="applicant.id", index=True)
    fit_score: Optional[float] = None  # 0-100, computed in code; None when not scored
    must_haves_met: int = 0
    must_haves_total: int = 0  # (ext)
    status: str = STATUS_NEEDS_REVIEW  # current status (changes only through a logged override)
    auto_status: str = STATUS_NEEDS_REVIEW  # (ext) the automated decision, kept after any override
    rank: Optional[int] = None  # 1 = best among eligible applicants; None for needs_review
    summary: str = ""
    strengths_json: str = "[]"  # (ext)
    gaps_json: str = "[]"  # (ext)
    reasons_json: str = "{}"  # (ext) job-related reasons for the status (see scoring.build_reasons)
    breakdown_json: str = "[]"  # per-category score, weight share, and points (see scoring.CategoryResult)
    flags_json: str = "[]"  # (ext) guardrail flags that sent this applicant to review
    model_used: str = ""
    provider_used: str = ""
    raw_response_json: str = ""  # the model's final raw reply
    output_valid: bool = False  # (ext) true when the reply passed validation (cacheable)
    cache_key: str = Field(default="", index=True)
    from_cache: bool = False  # (ext)
    tokens: int = 0  # (ext)
    cost: float = 0.0  # (ext)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CriterionScore(SQLModel, table=True):
    """The AI's 0-4 score for one criterion, with verbatim evidence quotes."""

    __tablename__ = "criterion_score"

    id: Optional[int] = Field(default=None, primary_key=True)
    evaluation_id: int = Field(foreign_key="evaluation.id", index=True)
    criterion_id: int = Field(foreign_key="criterion.id")
    score: int  # 0-4
    rationale: str = ""
    evidence_json: str = "[]"  # list of {"quote": str, "verified": bool}
    evidence_verified: bool = True  # every quote was found in the masked resume


class Override(SQLModel, table=True):
    """A human decision that changed an evaluation's status. Append-only."""

    __tablename__ = "override"

    id: Optional[int] = Field(default=None, primary_key=True)
    evaluation_id: int = Field(foreign_key="evaluation.id", index=True)
    from_status: str
    to_status: str
    reason: str
    created_at: datetime = Field(default_factory=utcnow)


class AuditEvent(SQLModel, table=True):
    """One step in the audit trail. Append-only: there is no update or delete path anywhere."""

    __tablename__ = "audit_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=utcnow, index=True)
    event_type: str = Field(index=True)
    run_id: Optional[int] = Field(default=None, index=True)
    role_id: Optional[int] = Field(default=None, index=True)
    applicant_id: Optional[int] = Field(default=None, index=True)
    model: Optional[str] = None
    provider: Optional[str] = None
    prompt_version: Optional[str] = None
    input_hash: Optional[str] = None
    payload_json: str = "{}"


# --- Candidate side ------------------------------------------------------------------------------
# These tables are separate from the employer tables above: no foreign key links a candidate to a role,
# an applicant, or a screening run, and candidate pages never query employer tables.

FACT_CATEGORIES = CATEGORIES
VERSION_SOURCES = ("ai", "template")  # who wrote the wording: the model (guard passed) or the fact template


class CandidateProfile(SQLModel, table=True):
    """A candidate's own workspace. Reached with an access code; there are no accounts in the MVP."""

    __tablename__ = "candidate_profile"

    id: Optional[int] = Field(default=None, primary_key=True)
    access_code_hash: str = Field(index=True, unique=True)  # sha256 of the code; the code is shown once
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""  # city and state only; never a street address
    headline: str = ""  # e.g. "Business data analyst"
    consent_at: Optional[datetime] = None  # when the candidate accepted the AI notice
    consent_notice_version: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CoreFact(SQLModel, table=True):
    """One verified fact about the candidate. Drafts may only restate what is written here."""

    __tablename__ = "core_fact"

    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="candidate_profile.id", index=True)
    category: str = DEFAULT_CATEGORY  # skills | education | certifications | experience
    title: str  # the skill, degree, certification, or job title
    organization: str = ""  # employer, school, or issuing body
    start: str = ""  # free text, e.g. "2021" or "Jun 2021"
    end: str = ""  # free text, e.g. "2025" or "Present"
    details: str = ""  # one accomplishment or detail per line, in the candidate's own words
    order: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ResumeVersion(SQLModel, table=True):
    """A draft the candidate approved and saved. Only drafts that passed the guard can be saved."""

    __tablename__ = "resume_version"

    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="candidate_profile.id", index=True)
    label: str  # the candidate's name for this version, e.g. "Analyst v2"
    target_role_title: str = ""
    target_role_text: str = ""  # the job description the candidate pasted
    body: str = ""  # the resume as Markdown
    draft_json: str = "{}"  # the structured draft (see schemas.ResumeDraft)
    source: str = "ai"  # ai | template
    guard_json: str = "{}"  # the guard report at save time (always passed)
    facts_hash: str = ""  # sha256 of the core facts the draft was checked against
    model_used: str = ""
    provider_used: str = ""
    prompt_version: str = ""
    approved_at: datetime = Field(default_factory=utcnow)  # the candidate approved it on save
    created_at: datetime = Field(default_factory=utcnow)
