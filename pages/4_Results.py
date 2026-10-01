"""Results: Shortlist | Not shortlisted | Auto-rejected | Needs review, with evidence, reasons, and overrides."""

from collections import defaultdict

import pandas as pd
import streamlit as st
from sqlmodel import select

from talentsift import ui
from talentsift.models import (
    RUN_RUNNING,
    STATUS_AUTO_REJECTED,
    STATUS_NEEDS_REVIEW,
    STATUS_NOT_SHORTLISTED,
    STATUS_PENDING_AUTO_REJECT,
    STATUS_SHORTLISTED,
    Applicant,
    CriterionScore,
    Evaluation,
    Role,
    ScreeningRun,
    loads,
)
from talentsift.overrides import (
    MIN_REASON_LENGTH,
    OverrideError,
    confirm_pending_rejections,
    override_status,
    overrides_for_run,
)
from talentsift.roles import criteria_for_version
from talentsift.scoring import recover_stale_runs

ui.page_setup("Results", icon="🏆")


def evidence_lines(score: CriterionScore, limit: int | None = None) -> str:
    lines = []
    for item in loads(score.evidence_json, default=[])[:limit]:
        mark = "✓ verified" if item["verified"] else "⚠️ **not found in the resume**"
        lines.append(f"> “{item['quote']}”  \n> <small>{mark}</small>")
    return "\n\n".join(lines)


def scored_rows(evaluation, criteria, scores_by_eval):
    by_criterion = {s.criterion_id: s for s in scores_by_eval.get(evaluation.id, [])}
    return [(c, by_criterion[c.id]) for c in criteria if c.id in by_criterion]


def must_have_checklist(rows) -> None:
    must = [(c, s) for c, s in rows if c.type == "must_have"]
    if must:
        st.markdown(
            "  \n".join(
                f"{'✅' if s.score >= 2 else '❌'} {c.name} <small>({s.score}/4)</small>" for c, s in must
            ),
            unsafe_allow_html=True,
        )


def reasons_block(evaluation) -> None:
    reasons = loads(evaluation.reasons_json, default={})
    if not reasons:
        return
    st.markdown(f"**{reasons.get('headline', '')}.** {reasons.get('explanation', '')}")
    unmet = reasons.get("unmet_must_haves") or []
    if unmet:
        st.markdown("**Must-haves without enough evidence**")
        for item in unmet:
            st.markdown(f"- {item['criterion']} ({item['score']}/4): {item['rationale']}")
    weakest = [w for w in reasons.get("weakest_criteria") or [] if w["score"] <= 2]
    if weakest and evaluation.status != STATUS_SHORTLISTED:
        st.markdown("**Weakest criteria**")
        for item in weakest[:3]:
            st.markdown(f"- {item['criterion']} ({item['score']}/4): {item['rationale']}")


def detail(evaluation, applicant, rows) -> None:
    with st.expander("Candidate detail"):
        st.caption(
            f"File: {applicant.original_filename} · batch {applicant.batch} · model {evaluation.model_used or '—'} "
            f"via {evaluation.provider_used or '—'}{' · served from cache' if evaluation.from_cache else ''}"
        )
        if evaluation.auto_status != evaluation.status:
            st.info(f"Automated decision: {ui.status_label(evaluation.auto_status)} · changed by a manager to {ui.status_label(evaluation.status)}.")
        if rows:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "criterion": c.name,
                            "type": c.type.replace("_", " "),
                            "weight": c.weight,
                            "score": f"{s.score}/4",
                            "rationale": s.rationale,
                            "evidence": " | ".join(item["quote"] for item in loads(s.evidence_json, default=[])),
                            "verified": "yes" if s.evidence_verified else "NO",
                        }
                        for c, s in rows
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
            for c, s in rows:
                if s.score > 0:
                    st.markdown(f"**{c.name}** · {s.score}/4")
                    st.markdown(evidence_lines(s), unsafe_allow_html=True)
        if evaluation.summary:
            st.markdown(f"**AI summary:** {evaluation.summary}")
        gaps = loads(evaluation.gaps_json, default=[])
        if gaps:
            st.markdown("**Gaps:** " + "; ".join(gaps))


def override_form(session, evaluation, targets, *, label: str, key: str) -> None:
    if run_in_progress:
        st.caption("Available when the run finishes.")
        return
    key = f"{key}_{evaluation.status}"  # a new status gets a fresh, empty form
    with st.form(key):
        to_status = st.selectbox("Move to", targets, format_func=ui.status_label, key=f"{key}_to")
        reason = st.text_area(f"Reason (required, at least {MIN_REASON_LENGTH} characters)", height=70, key=f"{key}_reason")
        if st.form_submit_button(label, key=f"{key}_submit"):
            try:
                override_status(session, evaluation, to_status, reason)
                st.session_state.flash = f"Moved to {ui.STATUS_LABELS[to_status]}. The change and reason are logged."
                st.rerun()
            except OverrideError as exc:
                st.error(str(exc))


with ui.db_session() as session:
    recover_stale_runs(session)
    runs = list(session.exec(select(ScreeningRun).order_by(ScreeningRun.id.desc())))
    if not runs:
        st.info("No screening runs yet.")
        st.page_link("pages/3_Screen.py", label="Run a screen", icon="⚙️")
        st.stop()
    roles = {r.id: r for r in session.exec(select(Role))}
    wanted = st.session_state.get("run_id")
    index = next((i for i, r in enumerate(runs) if r.id == wanted), 0)
    run: ScreeningRun = st.selectbox(
        "Screening run",
        runs,
        index=index,
        format_func=lambda r: f"Run {r.id} · {roles[r.role_id].title} v{r.role_version} · {ui.fmt_time(r.started_at)} · {r.status}",
    )
    st.session_state.run_id = run.id
    run_in_progress = run.status == RUN_RUNNING
    if flash := st.session_state.pop("flash", None):
        st.success(flash)
    if run_in_progress:
        st.info("This run is still in progress. Statuses are final, and can be changed, once it finishes.")

    criteria = criteria_for_version(session, run.role_id, run.role_version)
    evaluations = list(session.exec(select(Evaluation).where(Evaluation.run_id == run.id)))
    applicants = {a.id: a for a in session.exec(select(Applicant).where(Applicant.id.in_([e.applicant_id for e in evaluations])))}
    scores_by_eval = defaultdict(list)
    for score in session.exec(select(CriterionScore).where(CriterionScore.evaluation_id.in_([e.id for e in evaluations]))):
        scores_by_eval[score.evaluation_id].append(score)

    providers = sorted({e.provider_used for e in evaluations if e.provider_used})
    st.caption(
        f"Model {run.model_requested} via {', '.join(providers) or '—'} · prompt {run.prompt_version} · "
        f"threshold {run.auto_reject_threshold} · top {run.top_n} · mode {run.auto_reject_mode} · "
        f"{run.total_tokens:,} tokens · ${run.total_cost:.4f} · {run.cache_hits} cached"
    )
    if run.status not in ("completed", RUN_RUNNING):
        st.error(f"This run {run.status.replace('_', ' ')}: {run.status_message}")

    def with_status(*statuses):
        chosen = [e for e in evaluations if e.status in statuses]
        return sorted(chosen, key=lambda e: (e.rank is None, e.rank or 0, -(e.fit_score or 0), e.applicant_id))

    shortlisted = with_status(STATUS_SHORTLISTED)
    middle = with_status(STATUS_NOT_SHORTLISTED)
    pending = with_status(STATUS_PENDING_AUTO_REJECT)
    rejected = with_status(STATUS_AUTO_REJECTED)
    review = with_status(STATUS_NEEDS_REVIEW)

    if pending:
        st.warning(
            f"Confirm mode: {len(pending)} applicant(s) are proposed for auto-rejection and nothing has been applied yet. "
            "Review them in the Auto-rejected tab, keep anyone who deserves a second look, then confirm."
        )

    tabs = st.tabs(
        [
            f"Shortlist ({len(shortlisted)})",
            f"Not shortlisted ({len(middle)})",
            f"Auto-rejected ({len(rejected) + len(pending)})",
            f"Needs review ({len(review)})",
        ]
    )

    with tabs[0]:
        if not shortlisted:
            st.info("Nobody is shortlisted in this run.")
        automated = [e for e in shortlisted if e.auto_status == STATUS_SHORTLISTED]
        if len(automated) > run.top_n:
            st.caption(
                f"{len(automated)} applicants are shortlisted instead of the top {run.top_n}: applicants tied with the "
                "last shortlisted score are all included, so ties are never broken by upload order."
            )
        for e in shortlisted:
            a, rows = applicants[e.applicant_id], scored_rows(e, criteria, scores_by_eval)
            with st.container(border=True):
                head, metric = st.columns([5, 1])
                rank = f"#{e.rank} · " if e.rank else ""
                head.markdown(f"#### {rank}{a.display_label}")
                metric.metric("Fit score", ui.fmt_score(e.fit_score))
                left, right = st.columns([2, 3])
                with left:
                    st.markdown(f"**Must-haves** ({e.must_haves_met}/{e.must_haves_total} met)")
                    must_have_checklist(rows)
                with right:
                    st.markdown("**Strengths with evidence**")
                    strong = sorted([(c, s) for c, s in rows if s.score >= 3], key=lambda cs: (-cs[1].score, cs[0].order))
                    for c, s in strong[:3]:
                        st.markdown(f"**{c.name}** · {s.score}/4")
                        st.markdown(evidence_lines(s, limit=1), unsafe_allow_html=True)
                    if not strong:
                        st.caption("No criterion scored 3 or higher.")
                if e.summary:
                    st.caption(e.summary)
                detail(e, a, rows)
                with st.expander("Override"):
                    override_form(session, e, [STATUS_NOT_SHORTLISTED, STATUS_NEEDS_REVIEW], label="Apply override", key=f"ov_{e.id}")

    with tabs[1]:
        if not middle:
            st.info("Nobody in this group.")
        for e in middle:
            a, rows = applicants[e.applicant_id], scored_rows(e, criteria, scores_by_eval)
            with st.container(border=True):
                head, metric = st.columns([5, 1])
                head.markdown(f"#### #{e.rank} · {a.display_label}" if e.rank else f"#### {a.display_label}")
                metric.metric("Fit score", ui.fmt_score(e.fit_score))
                reasons_block(e)
                detail(e, a, rows)
                with st.expander("Override"):
                    override_form(session, e, [STATUS_SHORTLISTED, STATUS_NEEDS_REVIEW], label="Apply override", key=f"ov_{e.id}")

    with tabs[2]:
        if pending and not run_in_progress:
            st.subheader(f"Proposed rejections ({len(pending)})")
            note = st.text_input("Note for the audit log (optional)", key=f"confirm_note_{run.id}")
            if st.button(f"Confirm {len(pending)} rejection(s)", type="primary"):
                count = confirm_pending_rejections(session, run, note=note)
                st.session_state.flash = f"{count} rejection(s) confirmed and logged."
                st.rerun()
        for e in pending + rejected:
            a, rows = applicants[e.applicant_id], scored_rows(e, criteria, scores_by_eval)
            with st.container(border=True):
                head, metric = st.columns([5, 1])
                tag = " · proposed" if e.status == STATUS_PENDING_AUTO_REJECT else ""
                head.markdown(f"#### {a.display_label}{tag}")
                metric.metric("Fit score", ui.fmt_score(e.fit_score))
                reasons_block(e)
                detail(e, a, rows)
                label = "Keep this applicant" if e.status == STATUS_PENDING_AUTO_REJECT else "Reinstate"
                with st.expander(label):
                    override_form(session, e, [STATUS_NOT_SHORTLISTED, STATUS_SHORTLISTED], label=label, key=f"ov_{e.id}")
        if not pending and not rejected:
            st.info("Nobody was auto-rejected in this run.")

    with tabs[3]:
        if not review:
            st.info("Nothing needs review.")
        for e in review:
            a, rows = applicants[e.applicant_id], scored_rows(e, criteria, scores_by_eval)
            with st.container(border=True):
                head, metric = st.columns([5, 1])
                head.markdown(f"#### {a.display_label}")
                metric.metric("Fit score", ui.fmt_score(e.fit_score), help="Not used for any automated decision.")
                for flag in loads(e.flags_json, default=[]):
                    st.warning(flag["detail"])
                if e.status != e.auto_status:
                    st.caption(f"Moved here by a manager (automated decision: {ui.status_label(e.auto_status)}).")
                detail(e, a, rows)
                with st.expander("Decide"):
                    override_form(session, e, [STATUS_SHORTLISTED, STATUS_NOT_SHORTLISTED], label="Record decision", key=f"ov_{e.id}")

    history = overrides_for_run(session, run)
    if history:
        st.subheader("Overrides in this run")
        by_eval = {e.id: e for e in evaluations}
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "when": ui.fmt_time(o.created_at),
                        "applicant": applicants[by_eval[o.evaluation_id].applicant_id].display_label,
                        "from": ui.STATUS_LABELS.get(o.from_status, o.from_status),
                        "to": ui.STATUS_LABELS.get(o.to_status, o.to_status),
                        "reason": o.reason,
                    }
                    for o in history
                ]
            ),
            hide_index=True,
            width="stretch",
        )
