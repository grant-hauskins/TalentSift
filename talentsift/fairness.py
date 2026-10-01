"""Fairness checks offered on the Audit page.

1. Consistency: re-score a sample of a run's applicants with the cache bypassed and compare scores, order,
   and status against the original run.
2. Name swap: two resumes that differ only in name must have identical masked text, identical scores, and
   the same status.

Checks score applicants in memory (no new ScreeningRun) and log their LLM calls plus a `fairness_check`
event, so they never change any applicant's status.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import Session, select

from talentsift.audit import event_payload, list_events, log_event
from talentsift.config import Settings
from talentsift.llm.base import LLMClient
from talentsift.models import (
    STATUS_AUTO_REJECTED,
    STATUS_NOT_SHORTLISTED,
    STATUS_PENDING_AUTO_REJECT,
    STATUS_SHORTLISTED,
    Applicant,
    CriterionScore,
    Evaluation,
    Role,
    ScreeningRun,
)
from talentsift.prompts import SCREEN_PROMPT, load_prompt
from talentsift.roles import criteria_for_version
from talentsift.scoring import RankInput, ScoredApplicant, assign_statuses, iter_scored, ranking_key


class FairnessCheckError(RuntimeError):
    pass


@dataclass
class ConsistencyRow:
    display_label: str
    original_fit: float | None
    new_fit: float | None
    original_status: str
    new_status: str
    criterion_scores_match: bool
    same_inputs: bool = True  # False when the masked text or prompt changed since the run (not comparable)


@dataclass
class ConsistencyReport:
    run_id: int
    rows: list[ConsistencyRow]
    scores_identical: bool
    order_identical: bool
    statuses_identical: bool
    inputs_changed: list[str] = field(default_factory=list)  # labels whose inputs changed since the run

    @property
    def passed(self) -> bool:
        comparable = any(r.same_inputs for r in self.rows)
        return comparable and self.scores_identical and self.order_identical and self.statuses_identical


def _status_from_run_policy(fit: float | None, must_haves: int, run: ScreeningRun, cutoff: list | None) -> str:
    """Where a score would land under the original run's threshold and shortlist cutoff."""
    if fit is None:
        return "needs_review"
    if fit < run.auto_reject_threshold:
        return STATUS_PENDING_AUTO_REJECT if run.auto_reject_mode == "confirm" else STATUS_AUTO_REJECTED
    if cutoff is not None and (fit, must_haves) >= tuple(cutoff):
        return STATUS_SHORTLISTED
    return STATUS_NOT_SHORTLISTED


def _sample(evaluations: list[Evaluation], size: int) -> list[Evaluation]:
    """Evenly spaced picks across the ranking, so strong, borderline, and weak fits are all covered."""
    ranked = sorted(evaluations, key=lambda e: (e.rank is None, e.rank or 0, e.applicant_id))
    if len(ranked) <= size:
        return ranked
    step = (len(ranked) - 1) / (size - 1)
    return [ranked[round(i * step)] for i in range(size)]


def _check_role_unchanged(session: Session, run: ScreeningRun) -> tuple[Role, list]:
    role = session.get(Role, run.role_id)
    criteria = criteria_for_version(session, run.role_id, run.role_version)
    if role is None or role.version != run.role_version:
        raise FairnessCheckError(
            "The rubric changed since this run, so a re-run would not be comparable. Run a new screening first."
        )
    return role, criteria


def consistency_check(
    session: Session, run: ScreeningRun, client: LLMClient, settings: Settings, sample_size: int = 5
) -> ConsistencyReport:
    role, criteria = _check_role_unchanged(session, run)
    if client.model != run.model_requested:
        raise FairnessCheckError(
            f"Run {run.id} used {run.model_requested}; the selected AI provider uses {client.model}. "
            "Pick the same provider and model to check consistency."
        )
    # Re-score under the run's own policy, so a later settings change does not look like model drift.
    settings = settings.with_overrides(must_have_penalty=run.must_have_penalty)
    evaluations = list(
        session.exec(select(Evaluation).where(Evaluation.run_id == run.id, Evaluation.fit_score != None))  # noqa: E711
    )
    if len(evaluations) < 2:
        raise FairnessCheckError("This run has fewer than two scored applicants to compare.")
    sample = _sample(evaluations, sample_size)
    applicants = [session.get(Applicant, e.applicant_id) for e in sample]
    ranking_event_cutoff = _run_cutoff(session, run)

    rescored = {
        s.applicant.id: s
        for s in iter_scored(
            session,
            role=role,
            criteria=criteria,
            applicants=applicants,
            client=client,
            settings=settings,
            prompt=load_prompt(SCREEN_PROMPT, settings.prompts_dir),
            use_cache=False,
            run_id=None,
            context={"check": "consistency", "original_run_id": run.id},
        )
    }

    rows = []
    for evaluation, applicant in zip(sample, applicants):
        new = rescored[applicant.id]
        old_scores = _criterion_scores(session, evaluation)
        new_scores = {c.criterion.id: c.score for c in new.checks}
        new_fit = new.fit.fit_score if new.fit else None
        new_status = "needs_review" if new.flags else _status_from_run_policy(
            new_fit, new.fit.must_haves_met if new.fit else 0, run, ranking_event_cutoff
        )
        rows.append(
            ConsistencyRow(
                display_label=applicant.display_label,
                original_fit=evaluation.fit_score,
                new_fit=new_fit,
                original_status=evaluation.auto_status,
                new_status=new_status,
                criterion_scores_match=old_scores == new_scores,
                same_inputs=new.cache_key == evaluation.cache_key,
            )
        )

    # Compare only applicants whose inputs are unchanged; changed inputs are reported, not called drift.
    comparable = [(e, r) for e, r in zip(sample, rows) if r.same_inputs]
    ids = {e.applicant_id for e, _ in comparable}
    original_order = [
        e.applicant_id
        for e in sorted((e for e, _ in comparable), key=lambda e: ranking_key(RankInput(e.applicant_id, e.fit_score, e.must_haves_met)))
    ]
    new_order = sorted(
        (s for s in rescored.values() if s.applicant.id in ids),
        key=lambda s: ranking_key(RankInput(s.applicant.id, s.fit.fit_score if s.fit else None, s.fit.must_haves_met if s.fit else 0)),
    )
    report = ConsistencyReport(
        run_id=run.id,
        rows=rows,
        scores_identical=all(r.criterion_scores_match and r.original_fit == r.new_fit for _, r in comparable),
        order_identical=original_order == [s.applicant.id for s in new_order],
        statuses_identical=all(r.original_status == r.new_status for _, r in comparable),
        inputs_changed=[r.display_label for r in rows if not r.same_inputs],
    )
    log_event(
        session,
        "fairness_check",
        run_id=run.id,
        role_id=run.role_id,
        model=client.model,
        payload={
            "client": client.provider,
            "check": "consistency",
            "passed": report.passed,
            "scores_identical": report.scores_identical,
            "order_identical": report.order_identical,
            "statuses_identical": report.statuses_identical,
            "inputs_changed": report.inputs_changed,
            "sample": [r.__dict__ for r in rows],
        },
    )
    session.commit()
    return report


@dataclass
class NameSwapReport:
    labels: tuple[str, str]
    masked_identical: bool
    scores_identical: bool
    status_identical: bool
    fit_scores: tuple[float | None, float | None]
    statuses: tuple[str, str]
    run_statuses: tuple[str, str] | None = None  # statuses in an existing run, when both were screened
    diff_preview: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        run_ok = self.run_statuses is None or self.run_statuses[0] == self.run_statuses[1]
        return self.masked_identical and self.scores_identical and self.status_identical and run_ok


def name_swap_check(
    session: Session,
    *,
    role: Role,
    first: Applicant,
    second: Applicant,
    client: LLMClient,
    settings: Settings,
    run: ScreeningRun | None = None,
) -> NameSwapReport:
    """Confirm two resumes that differ only in name are treated identically."""
    if not role.is_approved:
        raise FairnessCheckError("Approve the role's rubric before running the name-swap test.")
    criteria = criteria_for_version(session, role.id, role.version)
    scored: list[ScoredApplicant] = list(
        iter_scored(
            session,
            role=role,
            criteria=criteria,
            applicants=[first, second],
            client=client,
            settings=settings,
            prompt=load_prompt(SCREEN_PROMPT, settings.prompts_dir),
            use_cache=False,  # score both fresh: identical input must give identical output
            run_id=None,
            context={"check": "name_swap"},
        )
    )
    a, b = scored
    ranking = assign_statuses(
        [RankInput(s.applicant.id, s.fit.fit_score if s.fit else None, s.fit.must_haves_met if s.fit else 0, tuple(s.flags)) for s in scored],
        threshold=role.auto_reject_threshold,
        top_n=role.top_n,
        mode=settings.auto_reject_mode,
    )
    statuses = (ranking.decisions[first.id].status, ranking.decisions[second.id].status)

    run_statuses = None
    if run is not None:
        found = {
            e.applicant_id: e.auto_status
            for e in session.exec(select(Evaluation).where(Evaluation.run_id == run.id, Evaluation.applicant_id.in_([first.id, second.id])))
        }
        if len(found) == 2:
            run_statuses = (found[first.id], found[second.id])

    diff = [
        f"line {n}: {x!r} != {y!r}"
        for n, (x, y) in enumerate(zip(first.masked_text.splitlines(), second.masked_text.splitlines()), start=1)
        if x != y
    ][:5]
    report = NameSwapReport(
        labels=(first.display_label, second.display_label),
        masked_identical=first.masked_text == second.masked_text,
        scores_identical=[c.score for c in a.checks] == [c.score for c in b.checks]
        and (a.fit.fit_score if a.fit else None) == (b.fit.fit_score if b.fit else None),
        status_identical=statuses[0] == statuses[1],
        fit_scores=(a.fit.fit_score if a.fit else None, b.fit.fit_score if b.fit else None),
        statuses=statuses,
        run_statuses=run_statuses,
        diff_preview=diff,
    )
    log_event(
        session,
        "fairness_check",
        role_id=role.id,
        run_id=run.id if run else None,
        model=client.model,
        payload={
            "client": client.provider,
            "check": "name_swap",
            "passed": report.passed,
            "applicants": list(report.labels),
            "masked_identical": report.masked_identical,
            "scores_identical": report.scores_identical,
            "status_identical": report.status_identical,
            "fit_scores": list(report.fit_scores),
            "statuses": list(report.statuses),
            "run_statuses": list(run_statuses) if run_statuses else None,
        },
    )
    session.commit()
    return report


def _criterion_scores(session: Session, evaluation: Evaluation) -> dict[int, int]:
    rows = session.exec(select(CriterionScore).where(CriterionScore.evaluation_id == evaluation.id))
    return {row.criterion_id: row.score for row in rows}


def _run_cutoff(session: Session, run: ScreeningRun) -> list | None:
    events = list_events(session, run_id=run.id, event_types=["ranking_computed"], newest_first=True, limit=1)
    return event_payload(events[0]).get("cutoff") if events else None


def find_name_swap_candidates(session: Session) -> list[tuple[Applicant, Applicant]]:
    """Pairs of different applicants whose masked text is identical (what a name-swap pair looks like)."""
    by_text: dict[str, list[Applicant]] = {}
    for applicant in session.exec(select(Applicant).order_by(Applicant.id)):
        if applicant.masked_text:
            by_text.setdefault(applicant.masked_text, []).append(applicant)
    return [(group[0], group[1]) for group in by_text.values() if len(group) >= 2]


def latest_run_with(session: Session, role: Role, applicant_ids: list[int]) -> ScreeningRun | None:
    """The newest run for this role that screened all the given applicants."""
    runs = session.exec(select(ScreeningRun).where(ScreeningRun.role_id == role.id).order_by(ScreeningRun.id.desc()))
    for run in runs:
        screened = set(
            session.exec(select(Evaluation.applicant_id).where(Evaluation.run_id == run.id, Evaluation.applicant_id.in_(applicant_ids)))
        )
        if screened == set(applicant_ids):
            return run
    return None

