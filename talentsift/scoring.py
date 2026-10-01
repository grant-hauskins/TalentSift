"""Screening engine: prompt -> LLM -> validate -> verify evidence -> fit score -> rank -> status.

`run_screening` is the entry point used by the Screen page:

1. Snapshot the approved rubric and the policy (threshold, top N, auto-reject mode) into a ScreeningRun.
2. For each applicant, build the prompt from masked text only. Reuse a cached reply when the cache key
   matches; otherwise call the LLM. Calls run in parallel threads that never touch the database.
3. Validate the reply (retry once), verify every evidence quote, and compute the fit score in code.
4. Rank eligible applicants and assign statuses. Guardrail cases go to `needs_review`, never auto-reject.

The pure functions (`compute_fit_score`, `verify_quote`, `assign_statuses`, `build_reasons`) hold the
rules and are tested with hand-calculated examples.
"""

from __future__ import annotations

import json
import threading
import unicodedata
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterator, Sequence

from sqlmodel import Session, select

from talentsift.audit import log_event, log_llm_attempts, sha256_text
from talentsift.config import Settings
from talentsift.llm.base import LLMClient, ValidatedCall, call_with_validation, extract_json_object, schema_name
from talentsift.models import (
    PARSE_PARTIAL,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_RUNNING,
    RUN_STOPPED_COST_CAP,
    SCREENABLE_PARSE_STATUSES,
    STATUS_AUTO_REJECTED,
    STATUS_NEEDS_REVIEW,
    STATUS_NOT_SHORTLISTED,
    STATUS_PENDING_AUTO_REJECT,
    STATUS_SHORTLISTED,
    Applicant,
    Criterion,
    CriterionScore,
    Evaluation,
    Role,
    ScreeningRun,
    dumps,
    utcnow,
)
from talentsift.prompts import SCREEN_PROMPT, PromptTemplate, load_prompt
from talentsift.roles import current_criteria, rubric_hash, rubric_snapshot
from talentsift.schemas import ScreeningOutput, check_screening_output

WEIGHT_POINTS = {"high": 3, "medium": 2, "low": 1}
MAX_SCORE = 4
UNMET_MUST_HAVE_MAX_SCORE = 1  # a must-have scored 0 or 1 is "unmet" and costs MUST_HAVE_PENALTY points

# Guardrail flags. Any flag sends the applicant to needs_review instead of an automated decision.
FLAG_VALIDATION_FAILED = "validation_failed"
FLAG_LLM_ERROR = "llm_error"
FLAG_NOT_SCORED = "not_scored"
FLAG_EVIDENCE_UNVERIFIED = "evidence_unverified"
FLAG_PARTIAL_PARSE = "partial_parse"
FLAG_NOT_PARSED = "not_parsed"
FLAG_TRUNCATED = "input_truncated"


class ScreeningError(RuntimeError):
    """The run cannot start (e.g. the rubric is not approved). The message is shown to the manager."""


# --- Fit score ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CriterionResult:
    criterion_id: int
    type: str  # must_have | nice_to_have
    weight: str  # high | medium | low
    score: int  # 0-4


@dataclass(frozen=True)
class FitResult:
    fit_score: float  # 0-100 after the must-have penalty, rounded to 0.1
    raw_score: float  # weighted average before the penalty
    penalty_points: float
    must_haves_met: int
    must_haves_total: int
    unmet_must_have_ids: tuple[int, ...]


def compute_fit_score(results: Sequence[CriterionResult], must_have_penalty: float) -> FitResult:
    """Weighted average of 0-4 scores scaled to 0-100, minus a penalty per unmet must-have (floor 0)."""
    max_points = sum(WEIGHT_POINTS[r.weight] * MAX_SCORE for r in results)
    points = sum(WEIGHT_POINTS[r.weight] * r.score for r in results)
    raw = 100.0 * points / max_points if max_points else 0.0
    must_haves = [r for r in results if r.type == "must_have"]
    unmet = tuple(r.criterion_id for r in must_haves if r.score <= UNMET_MUST_HAVE_MAX_SCORE)
    penalty = must_have_penalty * len(unmet)
    return FitResult(
        fit_score=round(max(0.0, raw - penalty), 1),
        raw_score=round(raw, 1),
        penalty_points=penalty,
        must_haves_met=len(must_haves) - len(unmet),
        must_haves_total=len(must_haves),
        unmet_must_have_ids=unmet,
    )


# --- Evidence verification -----------------------------------------------------------------------------

_LOOKALIKES = str.maketrans(
    {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "‐": "-", "−": "-", " ": " "}
)


def normalize_for_match(text: str) -> str:
    """Case-fold, unify quote and dash characters, and collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).translate(_LOOKALIKES).casefold()
    return " ".join(text.split())


def verify_quote(quote: str, normalized_source: str) -> bool:
    """True if the quote appears in the (already normalized) masked resume text.

    Wrapping quotation marks and edge punctuation are ignored; the words themselves must match exactly.
    """
    needle = normalize_for_match(quote).strip(" \"'.,;:!?")
    return len(needle) >= 3 and needle in normalized_source


@dataclass
class QuoteCheck:
    quote: str
    verified: bool


@dataclass
class CriterionCheck:
    criterion: Criterion
    score: int
    rationale: str
    quotes: list[QuoteCheck]

    @property
    def verified(self) -> bool:
        return all(q.verified for q in self.quotes)


# --- Status assignment ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class RankInput:
    applicant_id: int
    fit_score: float | None
    must_haves_met: int
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    status: str
    rank: int | None


@dataclass
class Ranking:
    decisions: dict[int, Decision]
    cutoff: tuple[float, int] | None  # (fit score, must-haves met) of the last shortlisted applicant
    order: list[int]  # applicant ids of eligible applicants, best first


def ranking_key(item: RankInput) -> tuple:
    """Deterministic order: fit score, then must-haves met, then applicant id. Never by name."""
    return (-(item.fit_score or 0.0), -item.must_haves_met, item.applicant_id)


def assign_statuses(items: Sequence[RankInput], *, threshold: float, top_n: int, mode: str) -> Ranking:
    """Apply the status rules (docs/decisions.md D-008, D-009, D-010, D-013).

    * Any guardrail flag (or no score) -> needs_review, unranked.
    * Eligible applicants are ranked. Those at or above the threshold compete for the top N; anyone tied
      with the N-th applicant's (fit score, must-haves met) is shortlisted too.
    * Below the threshold -> auto_rejected (automatic mode) or pending_auto_reject (confirm mode).
    * Everyone else -> not_shortlisted.
    """
    decisions: dict[int, Decision] = {}
    eligible = sorted((i for i in items if not i.flags and i.fit_score is not None), key=ranking_key)
    for item in items:
        if item.flags or item.fit_score is None:
            decisions[item.applicant_id] = Decision(STATUS_NEEDS_REVIEW, None)

    above = [i for i in eligible if i.fit_score >= threshold]
    cutoff = None
    if above and top_n > 0:
        last = above[min(top_n, len(above)) - 1]
        cutoff = (last.fit_score, last.must_haves_met)
    rejected_status = STATUS_PENDING_AUTO_REJECT if mode == "confirm" else STATUS_AUTO_REJECTED

    for rank, item in enumerate(eligible, start=1):
        if item.fit_score < threshold:
            status = rejected_status
        elif cutoff is not None and (item.fit_score, item.must_haves_met) >= cutoff:
            status = STATUS_SHORTLISTED
        else:
            status = STATUS_NOT_SHORTLISTED
        decisions[item.applicant_id] = Decision(status, rank)
    return Ranking(decisions=decisions, cutoff=cutoff, order=[i.applicant_id for i in eligible])


# --- Scoring one batch -------------------------------------------------------------------------------------


@dataclass
class ScoredApplicant:
    """Everything known about one applicant after the LLM step (before the status decision)."""

    applicant: Applicant
    flags: dict[str, str] = field(default_factory=dict)  # flag code -> human-readable detail
    output: ScreeningOutput | None = None
    checks: list[CriterionCheck] = field(default_factory=list)
    fit: FitResult | None = None
    raw_text: str = ""
    model_used: str = ""
    provider_used: str = ""
    tokens: int = 0
    cost: float = 0.0
    llm_calls: int = 0
    from_cache: bool = False
    cache_key: str = ""


@dataclass
class _Job:
    applicant: Applicant
    resume_text: str  # masked text actually sent (possibly truncated)
    truncated: bool
    user_prompt: str
    cache_key: str
    cached: Evaluation | None = None
    cached_output: ScreeningOutput | None = None


class _Budget:
    """Shared between worker threads: stops new calls at the cost cap or after a fatal provider error."""

    def __init__(self, cap_usd: float):
        self.cap_usd = cap_usd
        self.spent = 0.0
        self.stop_kind = ""  # "", "cost_cap", or "fatal"
        self.stop_message = ""
        self._lock = threading.Lock()

    def try_start(self) -> bool:
        with self._lock:
            if self.stop_kind:
                return False
            if self.cap_usd > 0 and self.spent >= self.cap_usd:
                self.stop_kind = "cost_cap"
                self.stop_message = f"The batch cost cap of ${self.cap_usd:.2f} was reached."
                return False
            return True

    def record(self, call: ValidatedCall) -> None:
        with self._lock:
            self.spent += call.total_cost
            if call.fatal_error and not self.stop_kind:
                self.stop_kind = "fatal"
                self.stop_message = f"The AI provider rejected the request: {call.fatal_error}"


def criteria_prompt_json(criteria: Sequence[Criterion]) -> str:
    """The rubric as the model sees it. Weights stay out: they are applied in code, not by the model."""
    rows = [{"criterion_id": c.id, "name": c.name, "description": c.description, "type": c.type} for c in criteria]
    return json.dumps(rows, indent=1, ensure_ascii=False)


def cache_key_for(resume_text: str, role: Role, rubric_digest: str, prompt: PromptTemplate, model: str) -> str:
    """hash(masked text + role version + prompt version + model), see docs/decisions.md D-012."""
    parts = [resume_text, f"role={role.id}", f"version={role.version}", f"rubric={rubric_digest}", f"prompt={prompt.cache_id}", f"model={model}"]
    return sha256_text("\n␞".join(parts))


def _build_job(applicant: Applicant, role: Role, criteria: Sequence[Criterion], prompt: PromptTemplate, settings: Settings, model: str, rubric_digest: str) -> _Job:
    text = applicant.masked_text
    truncated = len(text) > settings.max_resume_chars
    if truncated:
        text = text[: settings.max_resume_chars]
    user = prompt.render_user(
        role_title=role.title,
        role_summary=role.summary or "(none)",
        criteria_json=criteria_prompt_json(criteria),
        applicant_label=applicant.display_label,
        resume_text=text,
    )
    return _Job(applicant, text, truncated, user, cache_key_for(text, role, rubric_digest, prompt, model))


def _find_cached(session: Session, job: _Job, expected_ids: list[int]) -> None:
    """Attach the newest valid cached evaluation for this cache key, if its reply still validates."""
    query = (
        select(Evaluation)
        .where(Evaluation.cache_key == job.cache_key, Evaluation.output_valid == True)  # noqa: E712
        .order_by(Evaluation.id.desc())
    )
    for evaluation in session.exec(query):
        try:
            output = ScreeningOutput.model_validate(extract_json_object(evaluation.raw_response_json) or {})
            check_screening_output(output, expected_ids)
        except ValueError:
            continue
        job.cached, job.cached_output = evaluation, output
        return


def _call_worker(job: _Job, client: LLMClient, system: str, budget: _Budget, expected_ids: list[int]) -> ValidatedCall | None:
    """Runs in a worker thread. No database access here."""
    if not budget.try_start():
        return None
    call = call_with_validation(
        client,
        system,
        job.user_prompt,
        ScreeningOutput,
        check=lambda output: check_screening_output(output, expected_ids),
    )
    budget.record(call)
    return call


def _apply_output(scored: ScoredApplicant, job: _Job, output: ScreeningOutput, criteria: Sequence[Criterion], settings: Settings) -> None:
    """Verify evidence, compute the fit score, and set guardrail flags."""
    normalized = normalize_for_match(job.resume_text)
    by_id = {item.criterion_id: item for item in output.criteria}
    scored.output = output
    scored.checks = [
        CriterionCheck(
            criterion=c,
            score=by_id[c.id].score,
            rationale=by_id[c.id].rationale.strip(),
            quotes=[QuoteCheck(q.strip(), verify_quote(q, normalized)) for q in by_id[c.id].evidence if q.strip()],
        )
        for c in criteria
    ]
    scored.fit = compute_fit_score(
        [CriterionResult(ch.criterion.id, ch.criterion.type, ch.criterion.weight, ch.score) for ch in scored.checks],
        settings.must_have_penalty,
    )
    unverified = sum(1 for ch in scored.checks for q in ch.quotes if not q.verified)
    if unverified:
        scored.flags[FLAG_EVIDENCE_UNVERIFIED] = (
            f"{unverified} evidence quote(s) could not be found in the resume, so the scores they support "
            "cannot be trusted."
        )
    if job.applicant.parse_status == PARSE_PARTIAL:
        scored.flags[FLAG_PARTIAL_PARSE] = job.applicant.parse_message or "Part of the resume could not be read."
    if job.truncated:
        scored.flags[FLAG_TRUNCATED] = (
            f"The resume is longer than {settings.max_resume_chars:,} characters and was cut off before scoring."
        )


def iter_scored(
    session: Session,
    *,
    role: Role,
    criteria: Sequence[Criterion],
    applicants: Sequence[Applicant],
    client: LLMClient,
    settings: Settings,
    prompt: PromptTemplate,
    use_cache: bool,
    run_id: int | None,
    budget: _Budget | None = None,
    context: dict | None = None,
) -> Iterator[ScoredApplicant]:
    """Score applicants in order, yielding each result as soon as it is ready.

    LLM calls run in parallel (up to `settings.llm_concurrency`); audit events are written here, on the
    calling thread, in applicant order. Used by `run_screening` and by the fairness checks.
    """
    budget = budget or _Budget(settings.cost_cap_usd)
    context = context or {}
    expected_ids = [c.id for c in criteria]
    digest = rubric_hash(role, criteria)
    jobs: list[_Job | None] = []
    for applicant in applicants:
        if applicant.parse_status not in SCREENABLE_PARSE_STATUSES:
            jobs.append(None)
            continue
        job = _build_job(applicant, role, criteria, prompt, settings, client.model, digest)
        if use_cache:
            _find_cached(session, job, expected_ids)
        jobs.append(job)

    with ThreadPoolExecutor(max_workers=settings.llm_concurrency) as pool:
        futures: dict[int, Future] = {
            index: pool.submit(_call_worker, job, client, prompt.system, budget, expected_ids)
            for index, job in enumerate(jobs)
            if job is not None and job.cached is None
        }
        for index, (applicant, job) in enumerate(zip(applicants, jobs)):
            scored = ScoredApplicant(applicant=applicant)
            label = {"display_label": applicant.display_label, **context}
            ids = dict(run_id=run_id, role_id=role.id, applicant_id=applicant.id)

            if job is None:
                scored.flags[FLAG_NOT_PARSED] = (
                    applicant.parse_message or f"The resume could not be parsed ({applicant.parse_status})."
                )
                yield scored
                continue

            scored.cache_key = job.cache_key
            if job.cached is not None:
                scored.from_cache = True
                scored.raw_text = job.cached.raw_response_json
                scored.model_used, scored.provider_used = job.cached.model_used, job.cached.provider_used
                log_event(
                    session,
                    "llm_response",
                    model=job.cached.model_used,
                    provider=job.cached.provider_used,
                    prompt_version=prompt.version,
                    input_hash=sha256_text(job.cached.raw_response_json),
                    payload={**label, "cached": True, "source_evaluation_id": job.cached.id, "cache_key": job.cache_key},
                    **ids,
                )
                _apply_output(scored, job, job.cached_output, criteria, settings)
            else:
                call: ValidatedCall | None = futures[index].result()
                if call is None:
                    scored.flags[FLAG_NOT_SCORED] = f"Not scored: {budget.stop_message}"
                else:
                    scored.llm_calls = len(call.attempts)
                    scored.tokens, scored.cost = call.total_tokens, call.total_cost
                    last = call.last_result
                    if last is not None:
                        scored.raw_text = last.raw_text
                        scored.model_used, scored.provider_used = last.model_used, last.provider_used
                    log_llm_attempts(
                        session,
                        call.attempts,
                        schema_name=schema_name(ScreeningOutput),
                        prompt_version=prompt.version,
                        model_requested=client.model,
                        context={**label, "cache_key": job.cache_key, "prompt_sha256": prompt.sha256},
                        **ids,
                    )
                    if call.ok:
                        _apply_output(scored, job, call.value, criteria, settings)
                    elif call.attempts[-1].call_error:
                        scored.flags[FLAG_LLM_ERROR] = f"The AI call failed: {call.attempts[-1].call_error}"
                    else:
                        scored.flags[FLAG_VALIDATION_FAILED] = (
                            f"The AI's answer failed validation twice ({call.last_error}), so no score was computed."
                        )

            for check in scored.checks:
                bad = [q.quote for q in check.quotes if not q.verified]
                if bad:
                    log_event(
                        session,
                        "evidence_unverified",
                        model=scored.model_used,
                        prompt_version=prompt.version,
                        payload={**label, "criterion_id": check.criterion.id, "criterion": check.criterion.name, "score": check.score, "unverified_quotes": bad},
                        **ids,
                    )
            session.commit()
            yield scored


# --- Reasons -----------------------------------------------------------------------------------------------


def _criterion_line(check: CriterionCheck) -> dict:
    return {
        "criterion": check.criterion.name,
        "type": check.criterion.type,
        "score": check.score,
        "rationale": check.rationale,
    }


def build_reasons(scored: ScoredApplicant, decision: Decision, *, threshold: float, top_n: int, cutoff: tuple[float, int] | None) -> dict:
    """Job-related reasons for a status, written so they could be shared with the applicant."""
    fit = scored.fit.fit_score if scored.fit else None
    unmet = [c for c in scored.checks if c.criterion.type == "must_have" and c.score <= UNMET_MUST_HAVE_MAX_SCORE]
    others = [c for c in scored.checks if c not in unmet]
    weakest = sorted(others, key=lambda c: (c.score, -WEIGHT_POINTS[c.criterion.weight], c.criterion.order))[:3]
    strongest = sorted(
        (c for c in scored.checks if c.score >= 3), key=lambda c: (-c.score, -WEIGHT_POINTS[c.criterion.weight], c.criterion.order)
    )[:3]
    must_total = scored.fit.must_haves_total if scored.fit else 0

    def names(checks: list[CriterionCheck]) -> str:
        return "; ".join(c.criterion.name for c in checks)

    status = decision.status
    if status in (STATUS_AUTO_REJECTED, STATUS_PENDING_AUTO_REJECT):
        headline = "Below the minimum fit score for this role"
        explanation = f"The fit score of {fit} is below this role's minimum of {threshold:g}."
        if unmet:
            explanation += f" The resume did not show enough evidence for {len(unmet)} of {must_total} must-have requirements: {names(unmet)}."
        low = [c for c in weakest if c.score <= 2]
        if low:
            explanation += f" Other areas with limited evidence: {names(low)}."
        if status == STATUS_PENDING_AUTO_REJECT:
            headline = "Proposed for auto-rejection, awaiting manager confirmation"
    elif status == STATUS_NOT_SHORTLISTED:
        headline = "Meets the minimum, ranked below the shortlist"
        explanation = f"The fit score of {fit} meets this role's minimum of {threshold:g}, but other applicants showed stronger evidence for the criteria."
        if cutoff is not None:
            explanation += f" The shortlist holds the top {top_n} applicants (lowest shortlisted score: {cutoff[0]})."
        if unmet:
            explanation += f" Must-have requirements without enough evidence: {names(unmet)}."
        elif weakest:
            explanation += f" Areas with the least evidence: {names(weakest[:2])}."
    elif status == STATUS_SHORTLISTED:
        headline = f"Shortlisted (rank {decision.rank})"
        explanation = f"Ranked {decision.rank} with a fit score of {fit}."
        if strongest:
            explanation += f" Strongest evidence: {names(strongest)}."
        if unmet:
            explanation += f" Must-haves without enough evidence: {names(unmet)}."
    else:
        headline = "Needs a human review before any decision"
        explanation = "This applicant was not decided automatically: " + " ".join(scored.flags.values())

    return {
        "status": status,
        "headline": headline,
        "explanation": explanation,
        "fit_score": fit,
        "threshold": threshold,
        "top_n": top_n,
        "rank": decision.rank,
        "unmet_must_haves": [_criterion_line(c) for c in unmet],
        "weakest_criteria": [_criterion_line(c) for c in weakest],
        "strongest_criteria": [_criterion_line(c) for c in strongest],
        "review_flags": [{"flag": code, "detail": detail} for code, detail in scored.flags.items()],
    }


# --- The run -----------------------------------------------------------------------------------------------

ProgressCallback = Callable[[int, int, ScoredApplicant, ScreeningRun], None]


def _save_evaluation(session: Session, run: ScreeningRun, scored: ScoredApplicant) -> Evaluation:
    output = scored.output
    evaluation = Evaluation(
        run_id=run.id,
        applicant_id=scored.applicant.id,
        fit_score=scored.fit.fit_score if scored.fit else None,
        must_haves_met=scored.fit.must_haves_met if scored.fit else 0,
        must_haves_total=scored.fit.must_haves_total if scored.fit else 0,
        status=STATUS_NEEDS_REVIEW,  # decided after everyone is scored
        summary=output.summary if output else "",
        strengths_json=dumps(output.strengths if output else []),
        gaps_json=dumps(output.gaps if output else []),
        flags_json=dumps([{"flag": code, "detail": detail} for code, detail in scored.flags.items()]),
        model_used=scored.model_used,
        provider_used=scored.provider_used,
        raw_response_json=scored.raw_text,
        output_valid=output is not None,
        cache_key=scored.cache_key,
        from_cache=scored.from_cache,
        tokens=scored.tokens,
        cost=scored.cost,
    )
    session.add(evaluation)
    session.flush()
    for check in scored.checks:
        session.add(
            CriterionScore(
                evaluation_id=evaluation.id,
                criterion_id=check.criterion.id,
                score=check.score,
                rationale=check.rationale,
                evidence_json=dumps([{"quote": q.quote, "verified": q.verified} for q in check.quotes]),
                evidence_verified=check.verified,
            )
        )
    if scored.fit:
        log_event(
            session,
            "score_computed",
            run_id=run.id,
            role_id=run.role_id,
            applicant_id=scored.applicant.id,
            model=scored.model_used,
            provider=scored.provider_used,
            prompt_version=run.prompt_version,
            payload={
                "display_label": scored.applicant.display_label,
                "evaluation_id": evaluation.id,
                "fit_score": scored.fit.fit_score,
                "raw_score": scored.fit.raw_score,
                "penalty_points": scored.fit.penalty_points,
                "must_haves_met": scored.fit.must_haves_met,
                "must_haves_total": scored.fit.must_haves_total,
                "from_cache": scored.from_cache,
                "flags": list(scored.flags),
                "criteria": [
                    {
                        "criterion_id": c.criterion.id,
                        "type": c.criterion.type,
                        "weight": c.criterion.weight,
                        "score": c.score,
                        "evidence_verified": c.verified,
                    }
                    for c in scored.checks
                ],
            },
        )
    return evaluation


def _decide(session: Session, run: ScreeningRun, results: list[tuple[ScoredApplicant, Evaluation]]) -> dict[str, int]:
    """Rank, assign statuses and reasons, and log ranking_computed and auto_reject events."""
    ranking = assign_statuses(
        [RankInput(e.applicant_id, e.fit_score, e.must_haves_met, tuple(s.flags)) for s, e in results],
        threshold=run.auto_reject_threshold,
        top_n=run.top_n,
        mode=run.auto_reject_mode,
    )
    counts: dict[str, int] = {}
    for scored, evaluation in results:
        decision = ranking.decisions[evaluation.applicant_id]
        reasons = build_reasons(scored, decision, threshold=run.auto_reject_threshold, top_n=run.top_n, cutoff=ranking.cutoff)
        evaluation.status = evaluation.auto_status = decision.status
        evaluation.rank = decision.rank
        evaluation.reasons_json = dumps(reasons)
        evaluation.updated_at = utcnow()
        counts[decision.status] = counts.get(decision.status, 0) + 1
        if decision.status == STATUS_AUTO_REJECTED:
            log_event(
                session,
                "auto_reject",
                run_id=run.id,
                role_id=run.role_id,
                applicant_id=evaluation.applicant_id,
                model=evaluation.model_used,
                provider=evaluation.provider_used,
                prompt_version=run.prompt_version,
                payload={
                    "display_label": scored.applicant.display_label,
                    "evaluation_id": evaluation.id,
                    "fit_score": evaluation.fit_score,
                    "threshold": run.auto_reject_threshold,
                    "mode": run.auto_reject_mode,
                    "reasons": reasons,
                },
            )
    by_applicant = {e.applicant_id: (s, e) for s, e in results}
    log_event(
        session,
        "ranking_computed",
        run_id=run.id,
        role_id=run.role_id,
        prompt_version=run.prompt_version,
        payload={
            "threshold": run.auto_reject_threshold,
            "top_n": run.top_n,
            "mode": run.auto_reject_mode,
            "cutoff": list(ranking.cutoff) if ranking.cutoff else None,
            "counts": counts,
            "ranking": [
                {
                    "applicant_id": applicant_id,
                    "display_label": by_applicant[applicant_id][0].applicant.display_label,
                    "rank": ranking.decisions[applicant_id].rank,
                    "fit_score": by_applicant[applicant_id][1].fit_score,
                    "must_haves_met": by_applicant[applicant_id][1].must_haves_met,
                    "status": ranking.decisions[applicant_id].status,
                }
                for applicant_id in ranking.order
            ],
            "needs_review": [
                {"applicant_id": e.applicant_id, "display_label": s.applicant.display_label, "flags": list(s.flags)}
                for s, e in results
                if ranking.decisions[e.applicant_id].status == STATUS_NEEDS_REVIEW
            ],
        },
    )
    return counts


def run_screening(
    session: Session,
    *,
    role: Role,
    applicants: Sequence[Applicant],
    client: LLMClient,
    settings: Settings,
    force_rescore: bool = False,
    batch_label: str = "",
    progress: ProgressCallback | None = None,
) -> ScreeningRun:
    """Screen applicants for a role's approved rubric and decide their statuses. Returns the finished run."""
    if not role.is_approved:
        raise ScreeningError(f"Version {role.version} of '{role.title}' is not approved. Approve the rubric first.")
    criteria = current_criteria(session, role)
    if not criteria:
        raise ScreeningError("This role has no criteria.")
    if not applicants:
        raise ScreeningError("No applicants to screen.")
    prompt = load_prompt(SCREEN_PROMPT, settings.prompts_dir)
    applicants = sorted(applicants, key=lambda a: a.id)

    run = ScreeningRun(
        role_id=role.id,
        role_version=role.version,
        model_requested=client.model,
        llm_provider=client.provider,
        prompt_version=prompt.version,
        batch_label=batch_label,
        auto_reject_threshold=role.auto_reject_threshold,
        top_n=role.top_n,
        auto_reject_mode=settings.auto_reject_mode,
        must_have_penalty=settings.must_have_penalty,
        cost_cap_usd=settings.cost_cap_usd,
        force_rescore=force_rescore,
        status=RUN_RUNNING,
        applicants_total=len(applicants),
    )
    session.add(run)
    session.flush()
    log_event(
        session,
        "run_started",
        run_id=run.id,
        role_id=role.id,
        model=client.model,
        provider=client.provider,
        prompt_version=prompt.version,
        input_hash=rubric_hash(role, criteria),
        payload={
            "rubric": rubric_snapshot(role, criteria),
            "prompt_sha256": prompt.sha256,
            "batch_label": batch_label,
            "applicants": [a.display_label for a in applicants],
            "not_screenable": [a.display_label for a in applicants if a.parse_status not in SCREENABLE_PARSE_STATUSES],
            "policy": {
                "auto_reject_threshold": run.auto_reject_threshold,
                "top_n": run.top_n,
                "auto_reject_mode": run.auto_reject_mode,
                "must_have_penalty": run.must_have_penalty,
                "cost_cap_usd": run.cost_cap_usd,
                "temperature": settings.llm_temperature,
            },
            "force_rescore": force_rescore,
            "concurrency": settings.llm_concurrency,
        },
    )
    session.commit()

    budget = _Budget(settings.cost_cap_usd)
    try:
        results: list[tuple[ScoredApplicant, Evaluation]] = []
        scored_iter = iter_scored(
            session,
            role=role,
            criteria=criteria,
            applicants=applicants,
            client=client,
            settings=settings,
            prompt=prompt,
            use_cache=not force_rescore,
            run_id=run.id,
            budget=budget,
        )
        for done, scored in enumerate(scored_iter, start=1):
            evaluation = _save_evaluation(session, run, scored)
            run.total_tokens += scored.tokens
            run.total_cost = round(run.total_cost + scored.cost, 6)
            run.llm_calls += scored.llm_calls
            run.cache_hits += int(scored.from_cache)
            run.applicants_scored += int(scored.fit is not None)
            session.commit()
            results.append((scored, evaluation))
            if progress:
                progress(done, len(applicants), scored, run)

        counts = _decide(session, run, results)
        if budget.stop_kind == "cost_cap":
            run.status = RUN_STOPPED_COST_CAP
            run.status_message = f"{budget.stop_message} Unscored applicants are in Needs review."
        elif budget.stop_kind == "fatal":
            run.status = RUN_FAILED
            run.status_message = f"{budget.stop_message} Unscored applicants are in Needs review."
        else:
            run.status = RUN_COMPLETED
        run.finished_at = utcnow()
        log_event(
            session,
            "run_finished",
            run_id=run.id,
            role_id=role.id,
            model=client.model,
            provider=client.provider,
            prompt_version=prompt.version,
            payload={
                "status": run.status,
                "message": run.status_message,
                "counts": counts,
                "applicants_total": run.applicants_total,
                "applicants_scored": run.applicants_scored,
                "llm_calls": run.llm_calls,
                "cache_hits": run.cache_hits,
                "total_tokens": run.total_tokens,
                "total_cost": run.total_cost,
            },
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        run = session.get(ScreeningRun, run.id)
        run.status = RUN_FAILED
        run.status_message = f"Unexpected error: {type(exc).__name__}: {exc}"
        run.finished_at = utcnow()
        session.commit()
        raise
    return run


def screenable(applicants: Sequence[Applicant]) -> list[Applicant]:
    return [a for a in applicants if a.parse_status in SCREENABLE_PARSE_STATUSES]
