"""Candidate draft: fact-anchored AI drafting for a target role, checked by the no-new-facts guard."""

import pandas as pd
import streamlit as st

from talentsift import ui
from talentsift.candidates import CandidateError, list_facts
from talentsift.drafting import generate_draft, save_version

settings = ui.candidate_page_setup(
    "Draft a resume",
    "Paste the job description. The assistant chooses and words your facts for that role. It cannot add anything.",
    icon="✍️",
)

with ui.db_session() as session:
    profile = ui.require_candidate(session)
    facts = list_facts(session, profile.id)
    if flash := st.session_state.pop("candidate_flash", None):
        st.success(flash)
        st.page_link("pages/9_Candidate_Versions.py", label="See and compare your saved versions", icon="🗂️")
    if not facts:
        st.info("You have no core facts yet. Add them on your profile, then come back to draft.")
        st.page_link("pages/7_Candidate_Profile.py", label="Go to Candidate Profile", icon="👤")
        st.stop()

    with st.form("draft_form"):
        role_title = st.text_input("Role you are applying for", placeholder="Business Data Analyst")
        role_text = st.text_area(
            "Job description",
            height=220,
            placeholder="Paste the posting here. It decides what goes first; it is never a source of content.",
        )
        draft_clicked = st.form_submit_button(f"Draft from my {len(facts)} facts", type="primary")

    if draft_clicked:
        client = ui.get_client()
        if client is not None:
            try:
                with st.spinner("Drafting, then checking every line against your facts..."):
                    st.session_state.candidate_draft = generate_draft(
                        session, profile, client, settings, role_title=role_title, role_text=role_text
                    )
            except CandidateError as exc:
                st.error(str(exc))

    outcome = st.session_state.get("candidate_draft")
    if outcome is None:
        st.stop()

    st.subheader("Your draft")
    (st.success if outcome.source == "ai" else st.warning)(outcome.message, icon="🛡️")
    cols = st.columns(3)
    cols[0].metric("No-new-facts check", "Passed")
    cols[1].metric("Lines checked", outcome.report.lines_checked)
    cols[2].metric("Wording", "AI, from your facts" if outcome.source == "ai" else "Your own wording")
    if outcome.blocked:
        with st.expander(f"What the check rejected before this draft ({len(outcome.blocked)})"):
            st.caption("None of this is in the draft below. It is listed so you can see the check working.")
            st.dataframe(
                pd.DataFrame([{"Where": v["where"], "Rejected": v["text"], "Why": v["message"]} for v in outcome.blocked]).drop_duplicates(),
                hide_index=True,
                width="stretch",
            )
    if outcome.report.notes:
        with st.expander(f"Wording to double-check ({len(outcome.report.notes)})"):
            st.caption("These words are not in your facts. They add no skill, credential, or number, but read them before approving.")
            for note in outcome.report.notes:
                st.markdown(f"- {note}")

    with st.container(border=True):
        st.markdown(outcome.body)

    st.download_button("Download as text (.md)", outcome.body, file_name="resume_draft.md", mime="text/markdown")

    st.markdown("**Approve and save this version**")
    with st.form("save_version"):
        label = st.text_input("Name this version", value=outcome.role_title, placeholder="Analyst roles")
        approved = st.checkbox("I have read this draft, and everything in it is accurate.")
        left, right = st.columns(2)
        save_clicked = left.form_submit_button("Approve and save", type="primary")
        discard_clicked = right.form_submit_button("Discard this draft")
    if save_clicked:
        try:
            version = save_version(session, profile, outcome, label=label, approved=approved)
        except CandidateError as exc:
            st.error(str(exc))
        else:
            del st.session_state["candidate_draft"]
            st.session_state.candidate_flash = f"Saved as '{version.label}'."
            st.rerun()
    if discard_clicked:
        del st.session_state["candidate_draft"]
        st.rerun()
