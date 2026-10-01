"""Audit: the append-only event log, raw prompt/response viewer, CSV export, and fairness checks."""

from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from sqlmodel import select

from talentsift import ui
from talentsift.audit import EVENT_TYPES, event_payload, events_to_csv, list_events, record_export
from talentsift.fairness import (
    FairnessCheckError,
    consistency_check,
    find_name_swap_candidates,
    latest_run_with,
    name_swap_check,
)
from talentsift.models import Applicant, Role, ScreeningRun

ALL = "All"

settings = ui.page_setup("Audit", "Every step is an append-only event: nothing here can be edited or deleted.", icon="🔍")


def log_export(row_count: int, filters: dict, csv_text: str) -> None:
    with ui.db_session() as export_session:
        record_export(export_session, row_count=row_count, filters=filters, csv_text=csv_text)


with ui.db_session() as session:
    runs = list(session.exec(select(ScreeningRun).order_by(ScreeningRun.id.desc())))
    applicants = list(session.exec(select(Applicant).order_by(Applicant.id)))
    roles = {r.id: r for r in session.exec(select(Role))}
    labels = {a.id: a.display_label for a in applicants}

    log_tab, viewer_tab, fairness_tab = st.tabs(["Event log", "Prompt and response viewer", "Fairness checks"])

    # --- Event log -----------------------------------------------------------------------------------
    with log_tab:
        c1, c2, c3 = st.columns([1, 1, 2])
        run_filter = c1.selectbox("Run", [ALL] + [r.id for r in runs], format_func=lambda r: r if r == ALL else f"Run {r}")
        applicant_filter = c2.selectbox("Applicant", [ALL] + [a.id for a in applicants], format_func=lambda a: a if a == ALL else labels[a])
        type_filter = c3.multiselect("Event types", EVENT_TYPES, placeholder="All event types")
        events = list_events(
            session,
            run_id=None if run_filter == ALL else run_filter,
            applicant_id=None if applicant_filter == ALL else applicant_filter,
            event_types=type_filter or None,
            newest_first=True,
        )
        st.caption(f"{len(events):,} event(s)")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "id": e.id,
                        "time": ui.fmt_time(e.timestamp),
                        "event": e.event_type,
                        "run": e.run_id,
                        "role": roles[e.role_id].title if e.role_id in roles else None,
                        "applicant": labels.get(e.applicant_id),
                        "model": e.model,
                        "provider": e.provider,
                        "prompt": e.prompt_version,
                        "input hash": (e.input_hash or "")[:12],
                        "payload": e.payload_json[:160],
                    }
                    for e in events[:2000]
                ]
            ),
            hide_index=True,
            width="stretch",
            height=420,
        )
        filters = {"run_id": run_filter, "applicant_id": applicant_filter, "event_types": type_filter}
        csv_text = events_to_csv(events)
        st.download_button(
            "Export these events to CSV",
            data=csv_text,
            file_name=f"talentsift_audit_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.csv",
            mime="text/csv",
            on_click=log_export,
            args=(len(events), {k: str(v) for k, v in filters.items()}, csv_text),
            help="The export itself is logged as an audit event.",
        )

    # --- Prompt and response viewer -------------------------------------------------------------------
    with viewer_tab:
        llm_events = list_events(session, event_types=["llm_request", "llm_response"], newest_first=True, limit=5000)
        pairs = sorted({(e.run_id, e.applicant_id, e.role_id) for e in llm_events}, key=lambda k: (-(k[0] or 0), k[1] or 0))
        if not pairs:
            st.info("No AI calls logged yet.")
        else:
            def pair_label(key):
                run_id, applicant_id, role_id = key
                who = labels.get(applicant_id, "rubric draft" if applicant_id is None else applicant_id)
                where = f"Run {run_id}" if run_id else "check or draft"
                role = roles[role_id].title if role_id in roles else "no role"
                return f"{where} · {who} · {role}"

            chosen = st.selectbox("Conversation", pairs, format_func=pair_label)
            conversation = [e for e in reversed(llm_events) if (e.run_id, e.applicant_id, e.role_id) == chosen]
            for event in conversation:
                payload = event_payload(event)
                if event.event_type == "llm_request":
                    st.markdown(f"**Request · attempt {payload.get('attempt')}** · {ui.fmt_time(event.timestamp)} · prompt {event.prompt_version} · model {event.model}")
                    with st.expander("System prompt"):
                        st.code(payload.get("system", ""), language="markdown", wrap_lines=True)
                    st.code(payload.get("user", ""), language="markdown", wrap_lines=True)
                else:
                    cached = " · served from cache" if payload.get("cached") else ""
                    st.markdown(
                        f"**Response · attempt {payload.get('attempt', '-')}{cached}** · model used {event.model} · "
                        f"provider {event.provider} · tokens {payload.get('total_tokens', 0)} · cost {payload.get('cost_usd')}"
                    )
                    if payload.get("used_fallback"):
                        st.warning("Structured outputs were not available for this model; plain JSON instructions were used.")
                    for problem in ("call_error", "validation_error"):
                        if payload.get(problem):
                            st.error(f"{problem.replace('_', ' ')}: {payload[problem]}")
                    if payload.get("raw_text"):
                        st.code(payload["raw_text"], language="json", wrap_lines=True)

    # --- Fairness checks ----------------------------------------------------------------------------------
    with fairness_tab:
        st.markdown(
            "**1. Consistency re-run.** Re-score a sample of a run's applicants with the cache bypassed and compare "
            "scores, order, and status. A drifting model shows up here."
        )
        if runs:
            c1, c2 = st.columns([3, 1])
            run = c1.selectbox("Run to check", runs, format_func=lambda r: f"Run {r.id} · {roles[r.role_id].title} v{r.role_version}")
            size = c2.number_input("Sample size", min_value=2, max_value=20, value=5)
            if st.button("Run consistency check", key="consistency_button"):
                client = ui.get_client()
                if client is not None:
                    try:
                        with st.spinner("Re-scoring the sample..."):
                            report = consistency_check(session, run, client, settings, sample_size=int(size))
                        (st.success if report.passed else st.error)(
                            f"{'Passed' if report.passed else 'Differences found'}: scores identical {report.scores_identical}, "
                            f"order identical {report.order_identical}, statuses identical {report.statuses_identical}."
                        )
                        if report.inputs_changed:
                            st.warning(
                                "Not comparable (masked text or prompt changed since the run, for example after "
                                f"re-masking): {', '.join(report.inputs_changed)}."
                            )
                        st.dataframe(
                            pd.DataFrame(
                                [
                                    {
                                        "applicant": r.display_label,
                                        "original fit": r.original_fit,
                                        "re-run fit": r.new_fit,
                                        "original status": r.original_status,
                                        "re-run status": r.new_status,
                                        "criterion scores match": r.criterion_scores_match,
                                        "same inputs": r.same_inputs,
                                    }
                                    for r in report.rows
                                ]
                            ),
                            hide_index=True,
                            width="stretch",
                        )
                    except FairnessCheckError as exc:
                        st.error(str(exc))
        else:
            st.caption("Run a screen first.")

        st.divider()
        st.markdown(
            "**2. Name-swap test.** Two resumes that differ only in the name must produce identical masked text, "
            "identical scores, and the same status."
        )
        approved = [r for r in roles.values() if r.is_approved]
        screenable = [a for a in applicants if a.masked_text]
        if approved and len(screenable) >= 2:
            candidates = find_name_swap_candidates(session)
            first_default, second_default = candidates[0] if candidates else (screenable[0], screenable[1])
            c1, c2, c3 = st.columns(3)
            role = c1.selectbox("Role", approved, format_func=lambda r: f"{r.title} · v{r.version}")
            fmt = lambda a: f"{a.display_label} · {a.original_filename}"  # noqa: E731
            first = c2.selectbox("Resume A", screenable, index=screenable.index(first_default), format_func=fmt)
            second = c3.selectbox("Resume B", screenable, index=screenable.index(second_default), format_func=fmt)
            if candidates:
                st.caption("Detected name-swap pair(s): " + ", ".join(f"{a.display_label} & {b.display_label}" for a, b in candidates))
            if st.button("Run name-swap test", key="name_swap_button"):
                client = ui.get_client()
                if client is not None and first.id != second.id:
                    run = latest_run_with(session, role, [first.id, second.id])
                    report = name_swap_check(session, role=role, first=first, second=second, client=client, settings=settings, run=run)
                    (st.success if report.passed else st.error)("Passed" if report.passed else "Failed")
                    st.markdown(
                        f"- Masked text identical: **{report.masked_identical}**\n"
                        f"- Scores identical: **{report.scores_identical}** (fit {report.fit_scores[0]} vs {report.fit_scores[1]})\n"
                        f"- Same status: **{report.status_identical}** ({report.statuses[0]} vs {report.statuses[1]})"
                        + (f"\n- Statuses in run {run.id}: {report.run_statuses[0]} vs {report.run_statuses[1]}" if report.run_statuses else "")
                    )
                    for line in report.diff_preview:
                        st.code(line)
                elif first.id == second.id:
                    st.error("Pick two different resumes.")
        else:
            st.caption("Needs an approved role and at least two parsed resumes.")
