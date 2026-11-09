"""Candidate versions: saved versions, side-by-side comparison, A/B scoring against a role, and suggestions."""

import pandas as pd
import streamlit as st

from talentsift import ui
from talentsift.candidates import CandidateError, fact_views, list_facts
from talentsift.drafting import compare_versions, delete_version, list_versions, version_stats
from talentsift.models import CATEGORY_LABELS
from talentsift.suggestions import LABELS as SUGGESTION_LABELS
from talentsift.suggestions import suggest
from talentsift.version_scoring import MAX_VERSIONS, score_versions

settings = ui.candidate_page_setup(
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

    # --- A/B scoring and suggestions -----------------------------------------------------------------
    st.subheader("Score versions against a role (A/B)")
    st.caption(
        "See which version matches a job description best. The posting is turned into criteria once, every version "
        "is scored on the same criteria, and the fit score is calculated in code."
    )
    default_pick = ids[-min(2, len(ids)):]
    with st.form("ab_form"):
        picked = st.multiselect(f"Versions to score (up to {MAX_VERSIONS})", ids, default=default_pick, format_func=name)
        latest = by_id[ids[-1]]
        ab_title = st.text_input("Role", value=latest.target_role_title)
        ab_text = st.text_area("Job description", value=latest.target_role_text, height=180)
        ab_clicked = st.form_submit_button("Score these versions", type="primary")
    if ab_clicked:
        client = ui.get_client()
        if client is not None:
            try:
                with st.spinner("Reading the job description and scoring each version..."):
                    st.session_state.candidate_ab = score_versions(
                        session, profile, [by_id[i] for i in picked], client, settings, role_title=ab_title, role_text=ab_text
                    )
            except CandidateError as exc:
                st.error(str(exc))

    result = st.session_state.get("candidate_ab")
    if result is not None and all(s.version.id in by_id for s in result.scores):
        best = result.best
        if not best:
            st.error("None of the versions could be scored: " + "; ".join(s.error for s in result.scores))
        elif len(result.scored) == 1:
            st.info(f"'{best[0].version.label}' scores {best[0].fit_score:.1f} out of 100 for this role.")
        elif len(best) > 1:
            st.info("These versions tie at " + f"{best[0].fit_score:.1f}: " + ", ".join(f"'{s.version.label}'" for s in best) + ".")
        else:
            st.success(f"'{best[0].version.label}' scores best for this role: {best[0].fit_score:.1f} out of 100.", icon="🏁")
        st.caption(
            f"An estimate from {result.model}, using {len(result.criteria)} criteria read from the job description and equal "
            "weights for skills, education, certifications, and experience. An employer sets their own criteria and "
            "weights, so this shows which version fits the posting better, not whether you will be shortlisted."
        )

        rows = []
        for item in result.scores:
            row = {"Version": item.version.label, "Fit score": "—" if item.fit is None else f"{item.fit_score:.1f}"}
            for part in (item.fit.breakdown() if item.fit else []):
                row[CATEGORY_LABELS[part["category"]]] = "—" if part["score"] is None else f"{part['score']:.0f}"
            if item.fit:
                row["Must-haves met"] = f"{item.fit.must_haves_met}/{item.fit.must_haves_total}"
            if item.error:
                row["Note"] = f"Not scored: {item.error}"
            elif item.unverified:
                row["Note"] = f"{item.unverified} score(s) rest on quotes not found in the version"
            rows.append(row)
        st.dataframe(pd.DataFrame(rows).fillna(""), hide_index=True, width="stretch")

        with st.expander("Scores by criterion"):
            table = []
            for criterion in result.criteria:
                row = {
                    "Criterion": criterion.name,
                    "Category": CATEGORY_LABELS[criterion.category],
                    "Type": criterion.type.replace("_", " "),
                }
                for item in result.scored:
                    line = next(l for l in item.lines if l.criterion.id == criterion.id)
                    row[item.version.label] = f"{line.score}/4"
                table.append(row)
            st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")

        st.markdown("**Suggestions**")
        st.caption(
            "Drawn from the scores above and from your own core facts. They never add a skill, credential, or experience: "
            "where your facts do not cover something, the suggestion says so."
        )
        facts = fact_views(list_facts(session, profile.id))
        for item in result.scored:
            suggestions = suggest(item, facts)
            with st.expander(f"{item.version.label}: {len(suggestions)} suggestion(s)", expanded=item in best):
                if not suggestions:
                    st.write("No criterion scored low. Nothing to suggest.")
                for suggestion in suggestions:
                    tag = "must-have" if suggestion.must_have else "nice to have"
                    st.markdown(
                        f"**{suggestion.criterion}** · {suggestion.score}/4 · {tag} · "
                        f"{CATEGORY_LABELS[suggestion.category]}  \n*{SUGGESTION_LABELS[suggestion.kind]}.* {suggestion.text}"
                    )
                    for fact_line in suggestion.fact_lines:
                        st.markdown(f"> {fact_line}")
        st.page_link("pages/7_Candidate_Profile.py", label="Edit your core facts", icon="👤")

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
