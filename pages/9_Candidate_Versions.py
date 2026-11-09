"""Candidate versions: saved, approved resume versions and a side-by-side comparison."""

import pandas as pd
import streamlit as st

from talentsift import ui
from talentsift.drafting import compare_versions, delete_version, list_versions, version_stats

ui.candidate_page_setup(
    "Saved versions",
    "Every version here passed the no-new-facts check and was approved by you.",
    icon="🗂️",
)

with ui.db_session() as session:
    profile = ui.require_candidate(session)
    versions = list_versions(session, profile.id)
    if flash := st.session_state.pop("candidate_flash", None):
        st.success(flash)
    if not versions:
        st.info("No saved versions yet. Draft a resume for a role, approve it, and it appears here.")
        st.page_link("pages/8_Candidate_Draft.py", label="Draft a resume", icon="✍️")
        st.stop()

    table = pd.DataFrame([version_stats(v) for v in versions]).drop(columns=["id"])
    table["Saved"] = table["Saved"].map(ui.fmt_time)
    st.dataframe(table, hide_index=True, width="stretch")

    by_id = {v.id: v for v in versions}
    ids = list(by_id)

    def name(version_id: int) -> str:
        return by_id[version_id].label

    st.subheader("Compare two versions")
    if len(versions) < 2:
        st.caption("Save a second version (for another role, or after updating your facts) to compare them.")
    else:
        left_col, right_col = st.columns(2)
        left_id = left_col.selectbox("Version A", ids, index=len(ids) - 2, format_func=name, key="cmp_left")
        right_id = right_col.selectbox("Version B", ids, index=len(ids) - 1, format_func=name, key="cmp_right")
        if left_id == right_id:
            st.caption("Pick two different versions.")
        else:
            comparison = compare_versions(by_id[left_id], by_id[right_id])
            if comparison.identical:
                st.info("These two versions have exactly the same text.")
            else:
                notes = [f"{comparison.lines_only_left} line(s) only in A", f"{comparison.lines_only_right} line(s) only in B"]
                if comparison.skills_only_left:
                    notes.append("skills only in A: " + ", ".join(comparison.skills_only_left))
                if comparison.skills_only_right:
                    notes.append("skills only in B: " + ", ".join(comparison.skills_only_right))
                if comparison.skills_reordered:
                    notes.append("same skills, different order")
                if not comparison.same_facts:
                    notes.append("your core facts changed between these versions")
                st.caption(" · ".join(notes))
            left_col, right_col = st.columns(2)
            for col, version in ((left_col, comparison.left), (right_col, comparison.right)):
                with col.container(border=True):
                    st.caption(f"{version.label} · {version.target_role_title or 'general'}")
                    st.markdown(version.body)
            if not comparison.identical:
                with st.expander("Line-by-line differences"):
                    st.code("\n".join(comparison.diff), language="diff")

    st.subheader("Open a version")
    chosen = by_id[st.selectbox("Version", ids, index=len(ids) - 1, format_func=name, key="open_version")]
    st.caption(
        f"Saved {ui.fmt_time(chosen.created_at)} · target role: {chosen.target_role_title or 'general'} · "
        + ("AI wording checked against your facts" if chosen.source == "ai" else "your own wording")
    )
    with st.container(border=True):
        st.markdown(chosen.body)
    left, right = st.columns(2)
    left.download_button("Download as text (.md)", chosen.body, file_name=f"{chosen.label}.md", mime="text/markdown")
    if right.button(f"Delete '{chosen.label}'"):
        delete_version(session, chosen)
        st.session_state.candidate_flash = f"Deleted '{chosen.label}'."
        st.rerun()
