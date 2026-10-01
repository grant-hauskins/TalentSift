"""Screen: pick an approved role and a batch, run the AI screen, and watch progress, tokens, and cost."""

import pandas as pd
import streamlit as st
from sqlmodel import select

from talentsift import ui
from talentsift.ingest import list_batches
from talentsift.models import SCREENABLE_PARSE_STATUSES, Applicant, Role, ScreeningRun
from talentsift.roles import list_roles
from talentsift.scoring import ScreeningError, recover_stale_runs, run_screening

settings = ui.page_setup(
    "Screen",
    "One AI call per applicant at temperature 0. Cached results are reused, so re-runs are consistent and cheap.",
    icon="⚙️",
)

with ui.db_session() as session:
    recover_stale_runs(session)
    roles = list_roles(session)
    approved = [r for r in roles if r.is_approved]
    drafts = [r for r in roles if not r.is_approved]
    if not approved:
        st.warning("No approved roles yet. Draft and approve a rubric on the Roles page first.")
        st.page_link("pages/1_Roles.py", label="Go to Roles", icon="📋")
        st.stop()
    if drafts:
        st.caption("Not available until approved: " + ", ".join(f"{r.title} (v{r.version})" for r in drafts))

    left, right = st.columns(2)
    role: Role = left.selectbox("Role", approved, format_func=lambda r: f"{r.title} · v{r.version}")
    batches = list_batches(session)
    if not batches:
        st.warning("No applicants yet. Add resumes on the Applicants page.")
        st.page_link("pages/2_Applicants.py", label="Go to Applicants", icon="📄")
        st.stop()
    chosen_batches = right.multiselect("Batches", batches, default=batches)

    applicants = list(
        session.exec(select(Applicant).where(Applicant.batch.in_(chosen_batches)).order_by(Applicant.id))
    ) if chosen_batches else []
    skipped = [a for a in applicants if a.parse_status not in SCREENABLE_PARSE_STATUSES]

    cols = st.columns(5)
    cols[0].metric("Applicants", len(applicants))
    cols[1].metric("Threshold", role.auto_reject_threshold)
    cols[2].metric("Top N", role.top_n)
    cols[3].metric("Auto-reject mode", settings.auto_reject_mode)
    cols[4].metric("Cost cap", f"${settings.cost_cap_usd:.2f}")
    if skipped:
        st.warning(
            f"{len(skipped)} resume(s) cannot be read and will go straight to Needs review: "
            + ", ".join(f"{a.display_label} ({a.parse_status})" for a in skipped)
        )

    force = st.checkbox("Force re-score (ignore cached results and call the AI again)")
    st.caption(
        "Keep this page open while the run works. Clicking another control or page stops the run: results so far "
        "are kept, the rest go to Needs review, and running again reuses everything already scored."
    )
    provider_note = ui.PROVIDER_LABELS[ui.selected_provider()]
    run_clicked = st.button(
        f"Run screening · {len(applicants)} applicant(s) · {provider_note}",
        type="primary",
        disabled=not applicants,
        key="run_screening",
    )

    if run_clicked:
        client = ui.get_client()
        if client is not None:
            bar = st.progress(0.0, text="Starting...")
            tally = st.empty()
            feed = st.container(height=260)

            def on_progress(done, total, scored, run):
                bar.progress(done / total, text=f"Scored {done} of {total}")
                tally.markdown(
                    f"**Tokens** {run.total_tokens:,} · **Cost** ${run.total_cost:.4f} · "
                    f"**AI calls** {run.llm_calls} · **Cache hits** {run.cache_hits}"
                )
                fit = ui.fmt_score(scored.fit.fit_score if scored.fit else None)
                note = " (cached)" if scored.from_cache else ""
                flags = f" · ⚠️ {', '.join(scored.flags)}" if scored.flags else ""
                feed.write(f"{scored.applicant.display_label}: fit {fit}{note}{flags}")

            try:
                run = run_screening(
                    session,
                    role=role,
                    applicants=applicants,
                    client=client,
                    settings=settings,
                    force_rescore=force,
                    batch_label=", ".join(chosen_batches),
                    progress=on_progress,
                )
                st.session_state.run_id = run.id
                if run.status == "completed":
                    st.success(f"Run {run.id} completed.")
                elif run.status == "interrupted":
                    st.warning(f"Run {run.id} was interrupted: {run.status_message}")
                else:
                    st.error(f"Run {run.id} {run.status.replace('_', ' ')}: {run.status_message}")
                st.page_link("pages/4_Results.py", label="Open the results", icon="🏆")
            except ScreeningError as exc:
                st.error(str(exc))

    st.subheader("Run history")
    runs = list(session.exec(select(ScreeningRun).order_by(ScreeningRun.id.desc())))
    titles = {r.id: r.title for r in roles}
    if runs:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "run": r.id,
                        "role": f"{titles.get(r.role_id, r.role_id)} v{r.role_version}",
                        "started": ui.fmt_time(r.started_at),
                        "status": r.status,
                        "model": r.model_requested,
                        "prompt": r.prompt_version,
                        "applicants": r.applicants_total,
                        "scored": r.applicants_scored,
                        "AI calls": r.llm_calls,
                        "cache hits": r.cache_hits,
                        "tokens": r.total_tokens,
                        "cost (USD)": round(r.total_cost, 4),
                    }
                    for r in runs
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("No runs yet.")
