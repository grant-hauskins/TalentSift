"""TalentSift home page and entry point: `streamlit run app.py`.

Streamlit lists every file in `pages/` in the sidebar. Each page (including this one) starts with
`ui.page_setup`, which draws the disclaimer banner and the AI provider picker.
"""

import streamlit as st
from sqlmodel import func, select

from talentsift import ui
from talentsift.models import Applicant, AuditEvent, Role, ScreeningRun

settings = ui.page_setup("TalentSift", icon="🧭")
st.markdown(
    "Employer-side resume screening. Define a role, ingest a folder of resumes, and get a ranked, "
    "explained, fully auditable shortlist. Every automated decision is logged and reversible."
)

with ui.db_session() as session:
    roles = session.exec(select(Role)).all()
    applicants = session.exec(select(func.count()).select_from(Applicant)).one()
    runs = session.exec(select(func.count()).select_from(ScreeningRun)).one()
    events = session.exec(select(func.count()).select_from(AuditEvent)).one()

    cols = st.columns(4)
    cols[0].metric("Roles", len(roles))
    cols[0].caption(f"{sum(r.is_approved for r in roles)} approved")
    cols[1].metric("Applicants", applicants)
    cols[2].metric("Screening runs", runs)
    cols[3].metric("Audit events", events)

    st.subheader("Workflow")
    steps = [
        ("pages/1_Roles.py", "1. Roles", "📋", "Paste a job posting or load it from a link. The AI drafts a rubric; you edit and approve it."),
        ("pages/2_Applicants.py", "2. Applicants", "📄", "Pick a folder of PDF resumes (or upload them). Personal details are masked."),
        ("pages/3_Screen.py", "3. Screen", "⚙️", "Score a batch against an approved rubric, with a live token and cost tally."),
        ("pages/4_Results.py", "4. Results", "🏆", "Shortlist with evidence, reasons for everyone else, overrides and reinstatement."),
        ("pages/5_Audit.py", "5. Audit", "🔍", "Every step, raw prompts and replies, CSV export, and fairness checks."),
        ("pages/6_Settings.py", "Settings", "🔑", "Add your OpenRouter API key and model to screen with a real AI model."),
    ]
    for page, label, icon, text in steps:
        left, right = st.columns([1, 4])
        left.page_link(page, label=label, icon=icon)
        right.write(text)

    if not roles and not applicants:
        st.subheader("Demo data")
        st.write("No data yet. Load two fictional roles and 20 fictional resumes to try the full workflow offline.")
        if st.button("Load demo data", type="primary"):
            from talentsift.demo import seed_demo

            with st.spinner("Loading demo roles and resumes..."):
                summary = seed_demo(session, settings)
            st.success(summary.message)
            st.rerun()

with st.expander("How TalentSift decides"):
    st.markdown(
        f"""
- **The AI scores, code decides.** For each criterion the model returns a 0-4 score, a rationale, and verbatim
  quotes from the masked resume. Quotes are checked against the resume text.
- **Fit score (0-100)** = weighted average of criterion scores (high = 3, medium = 2, low = 1), minus
  **{settings.must_have_penalty:g} points** for each must-have scored 0-1 (never below 0).
- **Ranking**: fit score, then must-haves met, then applicant id. Never by name.
- **Statuses**: the top N at or above the role's threshold are *shortlisted* (ties included); below the
  threshold is *auto-rejected*; everyone else is *not shortlisted*.
- **Guardrails**: failed validation, unverified evidence, partially parsed or truncated resumes, and unscored
  applicants always go to *needs review*, never to auto-reject.
- **Auto-reject mode**: `{settings.auto_reject_mode}`. In `confirm` mode you approve each batch of rejections first.
"""
    )
