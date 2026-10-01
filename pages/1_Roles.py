"""Roles: paste a posting -> AI-drafted rubric -> edit -> approve. Thresholds, duplication, versions."""

import json

import pandas as pd
import streamlit as st

from talentsift import ui
from talentsift.audit import event_payload, list_events
from talentsift.config import PROJECT_ROOT
from talentsift.models import CRITERION_TYPES, CRITERION_WEIGHTS
from talentsift.roles import (
    CriterionInput,
    RoleError,
    approve_role,
    create_role,
    current_criteria,
    discard_draft,
    duplicate_role,
    get_role,
    list_roles,
    save_role,
    version_history,
)
from talentsift.rubric import draft_rubric, redraft_rubric

SAMPLE_ROLES = sorted((PROJECT_ROOT / "data" / "samples" / "roles").glob("*.json"))
NEW_ROLE = 0

settings = ui.settings()
ui.page_header("Roles", "Define what you are hiring for. Screening only uses a rubric version you have approved.")

with ui.db_session() as session:
    roles = list_roles(session)
    ids = [NEW_ROLE] + [r.id for r in roles]
    labels = {NEW_ROLE: "➕ New role from a job posting"}
    labels.update({r.id: f"{r.title} · v{r.version} · {'approved' if r.is_approved else 'draft'}" for r in roles})
    # A widget's value can only be set before it is drawn, so handlers park the next selection here.
    if "next_role_id" in st.session_state:
        st.session_state.role_id = st.session_state.pop("next_role_id")
    if st.session_state.get("role_id") not in ids:
        st.session_state.role_id = roles[-1].id if roles else NEW_ROLE
    role_id = st.selectbox("Role", ids, format_func=labels.get, key="role_id")

    # --- New role ---------------------------------------------------------------------------------
    if role_id == NEW_ROLE:
        if SAMPLE_ROLES:
            st.write("Try a sample posting:")
            cols = st.columns(len(SAMPLE_ROLES))
            for col, path in zip(cols, SAMPLE_ROLES):
                profile = json.loads(path.read_text())
                if col.button(f"Load '{profile['title']}'", key=f"sample_{path.stem}"):
                    st.session_state.new_title = profile["title"]
                    st.session_state.new_posting = profile["posting_text"]
                    st.rerun()

        with st.form("new_role"):
            title = st.text_input("Role title (optional; the AI proposes one)", key="new_title")
            posting = st.text_area("Job posting", height=320, key="new_posting", placeholder="Paste the full posting here")
            left, right = st.columns(2)
            threshold = left.slider("Auto-reject threshold (fit score)", 0, 100, 40)
            top_n = right.number_input("Shortlist size (top N)", min_value=1, max_value=200, value=5)
            draft_clicked = st.form_submit_button("Draft rubric with AI", type="primary")
            blank_clicked = st.form_submit_button("Start with a blank rubric instead")

        if draft_clicked:
            client = ui.get_client()
            if client is not None:
                with st.spinner("The AI is drafting criteria from the posting..."):
                    outcome = draft_rubric(
                        session, client, settings, posting_text=posting, title=title,
                        auto_reject_threshold=threshold, top_n=int(top_n),
                    )
                if outcome.ok:
                    st.session_state.next_role_id = outcome.role.id
                    st.session_state.flash = "Rubric drafted. Review every criterion, then approve it."
                    st.rerun()
                st.error(outcome.error)
        if blank_clicked:
            try:
                role = create_role(
                    session,
                    title=title or "Untitled role",
                    posting_text=posting,
                    criteria=[CriterionInput("Edit this criterion", "Describe the resume evidence you need", "must_have", "high")],
                    auto_reject_threshold=threshold,
                    top_n=int(top_n),
                )
                st.session_state.next_role_id = role.id
                st.rerun()
            except RoleError as exc:
                st.error(str(exc))
        st.stop()

    # --- Existing role ------------------------------------------------------------------------------
    role = get_role(session, role_id)
    criteria = current_criteria(session, role)
    if flash := st.session_state.pop("flash", None):
        st.success(flash)

    if role.is_approved:
        st.success(f"Version {role.version} is approved and ready for screening.", icon="✅")
    elif role.approved_version:
        st.warning(
            f"Version {role.version} is a draft. Version {role.approved_version} was approved; approve this draft "
            "(or discard it) before screening again.",
            icon="📝",
        )
    else:
        st.warning(f"Version {role.version} is a draft. Review the criteria, then approve it to enable screening.", icon="📝")

    for criterion in criteria:
        if criterion.proxy_warning:
            st.error(
                f"**Possible proxy for a protected characteristic: {criterion.name}.** {criterion.proxy_warning} "
                "Remove or reword this criterion before approving.",
                icon="⚠️",
            )

    by_id = {c.id: c for c in criteria}
    with st.form(f"edit_{role.id}_{role.version}"):
        title = st.text_input("Title", role.title)
        summary = st.text_area("Summary (shown to the AI with the criteria)", role.summary, height=80)
        left, right = st.columns(2)
        threshold = left.slider(
            "Auto-reject threshold (fit score)", 0, 100, role.auto_reject_threshold,
            help="Applicants below this fit score are auto-rejected, unless a guardrail sends them to review.",
        )
        top_n = right.number_input("Shortlist size (top N)", min_value=1, max_value=200, value=role.top_n)

        st.markdown("**Criteria** · add, edit, or delete rows. Weights: high = 3, medium = 2, low = 1.")
        table = pd.DataFrame(
            [
                {
                    "id": c.id,
                    "name": c.name,
                    "description": c.description,
                    "type": c.type,
                    "weight": c.weight,
                    "source": "AI draft" if c.source == "ai_draft" else "Manager",
                    "flag": "⚠️ proxy risk" if c.proxy_warning else "",
                }
                for c in criteria
            ],
            columns=["id", "name", "description", "type", "weight", "source", "flag"],
        )
        edited = st.data_editor(
            table,
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            key=f"criteria_{role.id}_{role.version}",
            disabled=["source", "flag"],
            column_config={
                "id": None,  # hidden: links edited rows back to stored criteria
                "name": st.column_config.TextColumn("Criterion", required=True, width="medium"),
                "description": st.column_config.TextColumn("What evidence counts", width="large"),
                "type": st.column_config.SelectboxColumn("Type", options=list(CRITERION_TYPES), required=True, default="nice_to_have"),
                "weight": st.column_config.SelectboxColumn("Weight", options=list(CRITERION_WEIGHTS), required=True, default="medium"),
                "source": st.column_config.TextColumn("Source"),
                "flag": st.column_config.TextColumn("Flag"),
            },
        )
        with st.expander("Job posting"):
            posting = st.text_area("Posting text", role.posting_text, height=260, label_visibility="collapsed")
        saved = st.form_submit_button("Save changes", type="primary")

    if saved:
        rows = []
        for row in edited.to_dict("records"):
            stored = by_id.get(int(row["id"])) if pd.notna(row.get("id")) else None
            rows.append(
                CriterionInput(
                    name=str(row.get("name") or "").strip(),
                    description=str(row.get("description") or "").strip(),
                    type=row.get("type") or "nice_to_have",
                    weight=row.get("weight") or "medium",
                    source=stored.source if stored else "manager",
                    proxy_warning=stored.proxy_warning if stored else "",
                    id=stored.id if stored else None,
                )
            )
        try:
            outcome = save_role(
                session, role, title=title, summary=summary, posting_text=posting, criteria=rows,
                auto_reject_threshold=int(threshold), top_n=int(top_n),
            )
            st.session_state.flash = outcome.message
            st.rerun()
        except RoleError as exc:
            st.error(str(exc))

    st.caption(
        f"Fit score = weighted average of 0-4 criterion scores scaled to 0-100, minus {settings.must_have_penalty:g} "
        "points for each must-have scored 0-1. Changing the title, summary, or criteria of an approved rubric creates "
        "a new draft version; changing the threshold or top N does not."
    )

    actions = st.columns(4)
    if not role.is_approved and actions[0].button(f"Approve version {role.version}", type="primary"):
        try:
            approve_role(session, role)
            st.session_state.flash = f"Version {role.version} approved. You can screen applicants now."
            st.rerun()
        except RoleError as exc:
            st.error(str(exc))
    if role.approved_version and not role.is_approved and actions[1].button("Discard draft"):
        discard_draft(session, role)
        st.session_state.flash = f"Draft discarded. Back to approved version {role.version}."
        st.rerun()
    if actions[2].button("Re-draft with AI", help="Replace the criteria with a fresh AI draft of the posting (as a draft)."):
        client = ui.get_client()
        if client is not None:
            with st.spinner("Re-drafting..."):
                outcome = redraft_rubric(session, role, client, settings)
            if outcome.ok:
                st.session_state.flash = "New AI draft saved. Review it, then approve."
                st.rerun()
            st.error(outcome.error)
    if actions[3].button("Duplicate role"):
        copy = duplicate_role(session, role)
        st.session_state.next_role_id = copy.id
        st.session_state.flash = f"Created '{copy.title}' as a draft copy."
        st.rerun()

    with st.expander("Version history"):
        st.dataframe(pd.DataFrame(version_history(session, role)), hide_index=True)
        approvals = list_events(session, role_id=role.id, event_types=["rubric_approved"])
        for event in approvals:
            payload = event_payload(event)
            st.caption(f"Version {payload['version']} approved {ui.fmt_time(event.timestamp)} · rubric hash {payload['rubric_hash'][:12]}")
