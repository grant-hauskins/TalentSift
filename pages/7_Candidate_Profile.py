"""Candidate profile: AI notice and consent, then core facts (skills, education, certifications, experience)."""

import streamlit as st

from talentsift import ui
from talentsift.candidates import (
    CONSENT_NOTICE,
    CandidateError,
    add_fact,
    create_profile,
    delete_fact,
    delete_profile,
    find_profile,
    list_facts,
    update_fact,
    update_profile,
)
from talentsift.models import CATEGORIES, CATEGORY_LABELS

ui.candidate_page_setup(
    "Candidate profile",
    "Your core facts are the only source the drafting assistant may use. Enter what is true; leave out what is not.",
)

# What each field means, per category: (title label, organization label, show dates, details label, details help)
FIELDS = {
    "skills": ("Skill or tool", None, False, None, ""),
    "education": ("Degree or program", "School", True, "Details (optional, one per line)", "For example: honors, GPA, relevant coursework."),
    "certifications": ("Certification or license", "Issued by", True, None, ""),
    "experience": ("Job title", "Employer", True, "What you did (one accomplishment per line)", "Use your own words. Include real tools and numbers; the assistant cannot add any."),
}
EXAMPLES = {
    "skills": "SQL, Tableau, Excel",
    "education": "B.S. Business Analytics",
    "certifications": "Tableau Desktop Specialist",
    "experience": "Data Analyst",
}


def fact_form(category: str, key: str, fact=None) -> dict | None:
    """Inputs for one fact. Returns the entered values when the form is submitted."""
    title_label, org_label, dates, details_label, details_help = FIELDS[category]
    with st.form(key, clear_on_submit=fact is None):
        title = st.text_input(title_label, value=fact.title if fact else "", placeholder=EXAMPLES[category])
        organization = start = end = details = ""
        if org_label:
            organization = st.text_input(org_label, value=fact.organization if fact else "")
        if dates:
            left, right = st.columns(2)
            start = left.text_input("Start (optional)", value=fact.start if fact else "", placeholder="2021")
            end = right.text_input("End or date earned", value=fact.end if fact else "", placeholder="2025 or Present")
        if details_label:
            details = st.text_area(details_label, value=fact.details if fact else "", height=130, help=details_help)
        if st.form_submit_button("Save changes" if fact else f"Add to {CATEGORY_LABELS[category].lower()}", type="primary" if fact is None else "secondary"):
            return {"title": title, "organization": organization, "start": start, "end": end, "details": details}
    return None


with ui.db_session() as session:
    profile = ui.current_candidate(session)

    # --- No profile open: start one or return with a code -------------------------------------------
    if profile is None:
        start_tab, return_tab = st.tabs(["Start a profile", "Return with your access code"])
        with start_tab:
            with st.container(border=True):
                st.markdown(CONSENT_NOTICE)
            with st.form("new_profile"):
                full_name = st.text_input("Your name, as it should appear on your resume")
                headline = st.text_input("Headline (optional)", placeholder="Business data analyst")
                left, mid, right = st.columns(3)
                email = left.text_input("Email (optional)")
                phone = mid.text_input("Phone (optional)")
                location = right.text_input("City and state (optional)", placeholder="Chicago, IL", help="No street address or ZIP code.")
                consent = st.checkbox("I have read the notice above and agree to AI-assisted drafting from the facts I enter.")
                submitted = st.form_submit_button("Create my profile", type="primary")
            if submitted:
                try:
                    profile, code = create_profile(
                        session, full_name=full_name, consent=consent, email=email, phone=phone, location=location, headline=headline
                    )
                except CandidateError as exc:
                    st.error(str(exc))
                else:
                    st.session_state[ui.CANDIDATE_KEY] = profile.id
                    st.session_state.candidate_new_code = code
                    st.rerun()
        with return_tab:
            with st.form("return_profile"):
                code = st.text_input("Access code", placeholder="XXXX-XXXX-XXXX")
                if st.form_submit_button("Open my profile"):
                    found = find_profile(session, code)
                    if found is None:
                        st.error("No profile matches that code. Check it and try again.")
                    else:
                        st.session_state[ui.CANDIDATE_KEY] = found.id
                        st.rerun()
        st.stop()

    # --- Profile open -----------------------------------------------------------------------------
    if code := st.session_state.get("candidate_new_code"):
        with st.container(border=True):
            st.success("Profile created. Save this access code now: it is the only way back to your profile, and it is not shown again.", icon="🔑")
            st.code(code, language=None)
            if st.button("I have saved my code"):
                del st.session_state["candidate_new_code"]
                st.rerun()
    if flash := st.session_state.pop("candidate_flash", None):
        st.success(flash)

    with st.expander("Name and contact details", expanded=False):
        st.caption("Printed at the top of your resume. These are never sent to the AI model.")
        with st.form("edit_profile"):
            full_name = st.text_input("Name", profile.full_name)
            headline = st.text_input("Headline", profile.headline)
            left, mid, right = st.columns(3)
            email = left.text_input("Email", profile.email)
            phone = mid.text_input("Phone", profile.phone)
            location = right.text_input("City and state", profile.location, help="No street address or ZIP code.")
            if st.form_submit_button("Save details"):
                try:
                    update_profile(session, profile, full_name=full_name, email=email, phone=phone, location=location, headline=headline)
                    st.session_state.candidate_flash = "Details saved."
                    st.rerun()
                except CandidateError as exc:
                    st.error(str(exc))

    facts = list_facts(session, profile.id)
    st.subheader(f"Core facts ({len(facts)})")
    if not facts:
        st.info("Add your first fact below. Start with a job you have held and the skills you used in it.")
    tabs = st.tabs([f"{CATEGORY_LABELS[c]} ({sum(f.category == c for f in facts)})" for c in CATEGORIES])
    for tab, category in zip(tabs, CATEGORIES):
        with tab:
            for fact in [f for f in facts if f.category == category]:
                summary = " · ".join(part for part in (fact.title, fact.organization, " – ".join(p for p in (fact.start, fact.end) if p)) if part)
                with st.expander(summary):
                    values = fact_form(category, f"edit_fact_{fact.id}", fact)
                    if values is not None:
                        try:
                            update_fact(session, fact, **values)
                            st.session_state.candidate_flash = f"Saved '{fact.title}'."
                            st.rerun()
                        except CandidateError as exc:
                            st.error(str(exc))
                    if st.button("Remove this fact", key=f"del_fact_{fact.id}"):
                        delete_fact(session, fact)
                        st.session_state.candidate_flash = "Fact removed."
                        st.rerun()
            st.markdown(f"**Add {CATEGORY_LABELS[category].lower()}**")
            values = fact_form(category, f"add_fact_{category}")
            if values is not None:
                try:
                    add_fact(session, profile, category=category, **values)
                    st.session_state.candidate_flash = "Fact added."
                    st.rerun()
                except CandidateError as exc:
                    st.error(str(exc))

    st.divider()
    left, right = st.columns(2)
    left.page_link("pages/8_Candidate_Draft.py", label="Next: draft a resume for a role", icon="✍️")
    if right.button("Close my profile on this device"):
        st.session_state.pop(ui.CANDIDATE_KEY, None)
        st.session_state.pop("candidate_draft", None)
        st.rerun()

    with st.expander("Your data and consent"):
        st.caption(f"You accepted notice version {profile.consent_notice_version} on {ui.fmt_time(profile.consent_at)}.")
        st.markdown(CONSENT_NOTICE)
        st.markdown("**Delete everything.** This removes your profile, your core facts, and every saved version. It cannot be undone.")
        sure = st.checkbox("I understand this cannot be undone.", key="delete_sure")
        if st.button("Delete my profile and all my data", disabled=not sure):
            delete_profile(session, profile)
            for key in (ui.CANDIDATE_KEY, "candidate_draft", "candidate_new_code"):
                st.session_state.pop(key, None)
            st.rerun()
