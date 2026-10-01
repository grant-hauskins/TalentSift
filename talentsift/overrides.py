"""Human decisions on top of the automated ones: overrides, reinstatements, and confirm-mode approvals.

Every change requires a written reason, creates an append-only `Override` row, and logs an `override`
event. The automated decision stays visible in `Evaluation.auto_status`.
"""

from __future__ import annotations

from sqlmodel import Session, select

from talentsift.audit import log_event
from talentsift.models import (
    RUN_RUNNING,
    STATUS_AUTO_REJECTED,
    STATUS_NEEDS_REVIEW,
    STATUS_NOT_SHORTLISTED,
    STATUS_PENDING_AUTO_REJECT,
    STATUS_SHORTLISTED,
    Applicant,
    Evaluation,
    Override,
    ScreeningRun,
    loads,
    utcnow,
)

MIN_REASON_LENGTH = 10
# A person can move an applicant to any of these. Rejection by a person is "not_shortlisted":
# "auto_rejected" is reserved for the automated decision.
OVERRIDE_TARGETS = (STATUS_SHORTLISTED, STATUS_NOT_SHORTLISTED, STATUS_NEEDS_REVIEW)
REJECTED_STATUSES = (STATUS_AUTO_REJECTED, STATUS_PENDING_AUTO_REJECT)


class OverrideError(ValueError):
    """The override was refused; the message is shown to the manager."""


def _require_finished(run: ScreeningRun | None) -> None:
    """Statuses are decided when a run finishes, so a change made mid-run would be overwritten."""
    if run is not None and run.status == RUN_RUNNING:
        raise OverrideError("This run is still in progress. Wait for it to finish before changing statuses.")


def _require_reason(reason: str) -> str:
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON_LENGTH:
        raise OverrideError(f"A reason of at least {MIN_REASON_LENGTH} characters is required.")
    return reason


def override_status(session: Session, evaluation: Evaluation, to_status: str, reason: str) -> Override:
    """Change an evaluation's status with a logged reason."""
    reason = _require_reason(reason)
    _require_finished(session.get(ScreeningRun, evaluation.run_id))
    if to_status not in OVERRIDE_TARGETS:
        raise OverrideError(f"Status must be one of {OVERRIDE_TARGETS}.")
    if to_status == evaluation.status:
        raise OverrideError(f"This applicant is already {to_status.replace('_', ' ')}.")

    from_status = evaluation.status
    kind = "reinstate" if from_status in REJECTED_STATUSES else "override"
    evaluation.status = to_status
    evaluation.updated_at = utcnow()
    record = Override(evaluation_id=evaluation.id, from_status=from_status, to_status=to_status, reason=reason)
    session.add(record)
    session.flush()

    applicant = session.get(Applicant, evaluation.applicant_id)
    run = session.get(ScreeningRun, evaluation.run_id)
    log_event(
        session,
        "override",
        run_id=evaluation.run_id,
        role_id=run.role_id if run else None,
        applicant_id=evaluation.applicant_id,
        payload={
            "kind": kind,
            "override_id": record.id,
            "evaluation_id": evaluation.id,
            "display_label": applicant.display_label if applicant else None,
            "from_status": from_status,
            "to_status": to_status,
            "auto_status": evaluation.auto_status,
            "fit_score": evaluation.fit_score,
            "reason": reason,
        },
    )
    session.commit()
    return record


def reinstate(session: Session, evaluation: Evaluation, reason: str, to_status: str = STATUS_NOT_SHORTLISTED) -> Override:
    """One-click reinstatement of an auto-rejected (or pending) applicant. A reason is required."""
    if evaluation.status not in REJECTED_STATUSES:
        raise OverrideError("Only auto-rejected applicants can be reinstated.")
    return override_status(session, evaluation, to_status, reason)


def pending_rejections(session: Session, run: ScreeningRun) -> list[Evaluation]:
    query = select(Evaluation).where(
        Evaluation.run_id == run.id, Evaluation.status == STATUS_PENDING_AUTO_REJECT
    ).order_by(Evaluation.rank)
    return list(session.exec(query))


def confirm_pending_rejections(session: Session, run: ScreeningRun, note: str = "") -> int:
    """Confirm mode: the manager approves the batch, so the proposed rejections now apply."""
    _require_finished(run)
    pending = pending_rejections(session, run)
    for evaluation in pending:
        evaluation.status = STATUS_AUTO_REJECTED
        evaluation.updated_at = utcnow()
        applicant = session.get(Applicant, evaluation.applicant_id)
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
                "display_label": applicant.display_label if applicant else None,
                "evaluation_id": evaluation.id,
                "fit_score": evaluation.fit_score,
                "threshold": run.auto_reject_threshold,
                "mode": "confirm",
                "confirmed_by_manager": True,
                "note": note.strip(),
                "reasons": loads(evaluation.reasons_json, default={}),
            },
        )
    session.commit()
    return len(pending)


def overrides_for_run(session: Session, run: ScreeningRun) -> list[Override]:
    query = (
        select(Override)
        .join(Evaluation, Override.evaluation_id == Evaluation.id)
        .where(Evaluation.run_id == run.id)
        .order_by(Override.id)
    )
    return list(session.exec(query))
