"""Headless Streamlit tests: every page renders, the banner is everywhere, and key flows work end to end."""

import os
from pathlib import Path

import pytest
from sqlmodel import select
from streamlit.testing.v1 import AppTest

from talentsift import credentials
from talentsift.config import get_settings
from talentsift.db import create_db_engine, init_db, new_session
from talentsift.models import STATUS_AUTO_REJECTED, Evaluation
from talentsift.ui import BANNER

from tests.factories import MEDIUM, STRONG, WEAK, make_applicant, make_role
from tests.pdf_factory import RESUME_LINES, text_pdf

APP = str(Path(__file__).resolve().parent.parent / "app.py")
PAGES = [
    "pages/1_Roles.py",
    "pages/2_Applicants.py",
    "pages/3_Screen.py",
    "pages/4_Results.py",
    "pages/5_Audit.py",
    "pages/6_Settings.py",
]


@pytest.fixture
def database(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'ui.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("AUTO_REJECT_MODE", "automatic")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(credentials, "ENV_PATH", tmp_path / ".env")  # never touch the developer's .env
    get_settings.cache_clear()
    engine = create_db_engine(url)
    init_db(engine)
    yield engine
    get_settings.cache_clear()
    engine.dispose()


@pytest.fixture
def seeded(database):
    with new_session(database) as session:
        make_role(session, threshold=40, top_n=1)
        for text in (STRONG, MEDIUM, WEAK):
            make_applicant(session, text)
    return database


def open_app() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def test_empty_app_renders_every_page_with_banner_and_provider_picker(database):
    at = open_app()
    assert at.title[0].value == "TalentSift"
    for page in ["app.py", *PAGES]:
        at.switch_page(page).run()
        assert not at.exception, (page, at.exception)
        assert sum(BANNER in info.value for info in at.info) == 1, page
        assert at.sidebar.radio[0].label == "AI provider", page


def test_provider_choice_follows_the_manager_across_pages(database):
    at = open_app()
    at.sidebar.radio[0].set_value("fake").run()
    at.switch_page("pages/3_Screen.py").run()
    assert at.sidebar.radio[0].value == "fake"


def test_demo_loader_and_fairness_checks_from_the_ui(database):
    at = open_app()
    next(b for b in at.button if b.label == "Load demo data").click().run()
    assert not at.exception, at.exception
    with new_session(database) as session:
        assert seed_counts(session) == (2, 26)

    at.switch_page("pages/3_Screen.py").run()
    at.button(key="run_screening").click().run()
    at.switch_page("pages/5_Audit.py").run()
    at.button(key="name_swap_button").click().run()
    assert not at.exception, at.exception
    assert any(s.value == "Passed" for s in at.success)  # the planted name-swap pair is picked by default
    at.button(key="consistency_button").click().run()
    assert any(s.value.startswith("Passed: scores identical True") for s in at.success)


def seed_counts(session):
    from sqlmodel import func

    from talentsift.models import Applicant, Role

    roles = session.exec(select(func.count()).select_from(Role)).one()
    applicants = session.exec(select(func.count()).select_from(Applicant)).one()
    return roles, applicants


def test_screen_run_and_reinstate_flow(seeded):
    at = open_app()
    for page in PAGES:
        at.switch_page(page).run()
        assert not at.exception, (page, at.exception)

    at.switch_page("pages/3_Screen.py").run()
    at.button(key="run_screening").click().run()
    assert not at.exception, at.exception
    assert any("completed" in s.value for s in at.success)

    with new_session(seeded) as session:
        rejected = session.exec(select(Evaluation).where(Evaluation.status == STATUS_AUTO_REJECTED)).one()
    at.switch_page("pages/4_Results.py").run()
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs][:4] == ["Shortlist (1)", "Not shortlisted (1)", "Auto-rejected (1)", "Needs review (0)"]

    key = f"ov_{rejected.id}"
    at.text_area(key=f"{key}_reason").input("ok")
    at.button(key=f"{key}_submit").click().run()
    assert any("at least 10 characters" in e.value for e in at.error)  # a reason is required

    at.text_area(key=f"{key}_reason").input("Strong retail operations background worth a call")
    at.button(key=f"{key}_submit").click().run()
    assert not at.exception, at.exception
    with new_session(seeded) as session:
        assert session.get(Evaluation, rejected.id).status == "not_shortlisted"

    at.switch_page("pages/5_Audit.py").run()
    assert not at.exception, at.exception


def test_settings_page_saves_and_removes_the_api_key(database, tmp_path):
    saved = {name: os.environ.pop(name) for name in ("OPENROUTER_API_KEY", "LLM_MODEL") if name in os.environ}
    try:
        at = open_app()
        at.switch_page("pages/6_Settings.py").run()
        at.text_input[0].input("sk-or-v1-test0000key1111")
        at.text_input[1].input("vendor/test-model")
        at.button[0].click().run()  # the form's submit button
        assert not at.exception, at.exception
        assert any("Saved" in s.value for s in at.success)
        assert "sk-or-v1-test0000key1111" in (tmp_path / ".env").read_text()
        assert at.sidebar.radio[0].value == "openrouter"
        assert not at.sidebar.error  # key and model are both set now
        assert all("sk-or-v1-test0000key1111" not in m.value for m in at.metric)

        next(b for b in at.button if b.label == "Remove stored key").click().run()
        assert "OPENROUTER_API_KEY" not in (tmp_path / ".env").read_text()
        assert at.sidebar.radio[0].value == "fake"
    finally:
        for name in ("OPENROUTER_API_KEY", "LLM_MODEL", "LLM_PROVIDER"):
            os.environ.pop(name, None)
        os.environ.update(saved)
        os.environ["LLM_PROVIDER"] = "fake"


def test_folder_import_previews_and_sends_only_pdfs(database, tmp_path):
    folder = tmp_path / "resumes"
    folder.mkdir()
    text_pdf(folder / "a.pdf", [RESUME_LINES])
    (folder / "b.docx").write_bytes(b"PK fake")
    (folder / "c.pdf").write_text("not really a pdf")

    at = open_app()
    at.switch_page("pages/2_Applicants.py").run()
    at.text_input(key="folder_path_box").set_value(str(folder)).run()
    assert not at.exception, at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics == {"PDFs to send": "1", "Already imported": "0", "Ignored (not PDF)": "2"}

    at.button(key="import_folder").click().run()
    assert not at.exception, at.exception
    with new_session(database) as session:
        from talentsift.models import Applicant

        assert [a.original_filename for a in session.exec(select(Applicant))] == ["a.pdf"]
    assert {m.label: m.value for m in at.metric}["Already imported"] == "1"


def test_roles_page_imports_a_posting_from_a_link(database, monkeypatch):
    from talentsift import job_import
    from talentsift.audit import list_events

    def fake_import(url):
        return job_import.ImportedPosting(url=url, title="Operations Coordinator", text="Run the warehouse. " * 20,
                                          method="structured data")

    monkeypatch.setattr(job_import, "import_posting", fake_import)
    at = open_app()
    at.switch_page("pages/1_Roles.py").run()
    at.text_input[0].input("https://jobs.example.com/42")
    next(b for b in at.button if b.label == "Load").click().run()
    assert not at.exception, at.exception
    assert at.text_input(key="new_title").value == "Operations Coordinator"
    assert at.text_area(key="new_posting").value.startswith("Run the warehouse.")
    assert any("structured data" in s.value for s in at.success)
    with new_session(database) as session:
        assert len(list_events(session, event_types=["posting_imported"])) == 1


# --- Employer upgrades: category weights and the model list (T18, T11) ---------------------------------


def test_roles_page_sets_category_weights_and_results_show_the_breakdown(seeded):
    from talentsift.models import Role
    from talentsift.roles import approve_role

    at = open_app()
    at.switch_page("pages/1_Roles.py").run()
    assert not at.exception, at.exception
    weights = {n.label: n for n in at.number_input if n.label in ("Skills", "Education", "Certifications", "Experience")}
    assert len(weights) == 4 and weights["Skills"].value == 25
    weights["Skills"].set_value(70)
    next(b for b in at.button if b.label == "Save changes").click().run()
    assert not at.exception, at.exception
    assert any("Saved as version 2" in s.value for s in at.success)

    with new_session(seeded) as session:
        role = session.exec(select(Role)).one()
        assert role.version == 2 and role.category_weights["skills"] == 70
        approve_role(session, role)

    at.switch_page("pages/3_Screen.py").run()
    assert any("Employer weights" in c.value for c in at.caption)
    next(b for b in at.button if b.key == "run_screening").click().run()
    assert not at.exception, at.exception
    at.switch_page("pages/4_Results.py").run()
    assert not at.exception, at.exception
    assert any("Employer weights for this run" in c.value for c in at.caption)
    assert any("Score breakdown (category score × employer weight)" in c.value for c in at.caption)
    assert any(m.value == "**Score breakdown**" for m in at.markdown)


def test_settings_page_offers_the_model_list_with_claude_as_default(database):
    from talentsift.model_catalog import DEFAULT_MODEL, MODEL_CATALOG

    at = open_app()
    at.switch_page("pages/6_Settings.py").run()
    assert not at.exception, at.exception
    picker = next(s for s in at.selectbox if s.label == "AI model for scoring")
    assert picker.value == DEFAULT_MODEL == "anthropic/claude-sonnet-4.5"
    assert picker.options[: len(MODEL_CATALOG)] == [option.label for option in MODEL_CATALOG]

    at.text_input[0].input("sk-or-v1-abc123def456")
    picker.set_value(MODEL_CATALOG[1].slug)
    next(b for b in at.button if b.label == "Save and use OpenRouter").click().run()
    assert not at.exception, at.exception
    assert os.environ["LLM_MODEL"] == MODEL_CATALOG[1].slug


# --- Candidate side (T25) ------------------------------------------------------------------------------

CANDIDATE_PAGES = ["pages/7_Candidate_Profile.py", "pages/8_Candidate_Draft.py", "pages/9_Candidate_Versions.py"]


def labelled(elements, label):
    return next(e for e in elements if e.label == label)


def test_candidate_pages_render_with_their_own_banner_and_ask_for_a_profile(database):
    from talentsift.ui import CANDIDATE_BANNER

    at = open_app()
    for page in CANDIDATE_PAGES:
        at.switch_page(page).run()
        assert not at.exception, (page, at.exception)
        assert sum(CANDIDATE_BANNER in info.value for info in at.info) == 1, page
        assert not any(BANNER in info.value for info in at.info), page
    assert any("Open your profile first" in w.value for w in at.warning)  # draft and versions need a profile


def test_candidate_cannot_start_without_consent(database):
    from talentsift.models import CandidateProfile

    at = open_app()
    at.switch_page(CANDIDATE_PAGES[0]).run()
    assert any("how TalentSift uses AI" in m.value for m in at.markdown)
    labelled(at.text_input, "Your name, as it should appear on your resume").set_value("Avery Exampleton")
    labelled(at.button, "Create my profile").click().run()
    assert any("accept the notice" in e.value for e in at.error)
    with new_session(database) as session:
        assert session.exec(select(CandidateProfile)).all() == []


def test_candidate_flow_profile_facts_draft_approve_save_compare(database):
    from talentsift.models import CandidateProfile, CoreFact, ResumeVersion

    at = open_app()
    at.switch_page(CANDIDATE_PAGES[0]).run()
    labelled(at.text_input, "Your name, as it should appear on your resume").set_value("Avery Exampleton")
    at.checkbox[0].check()
    labelled(at.button, "Create my profile").click().run()
    assert not at.exception, at.exception
    code = at.code[0].value
    assert len(code) == 14 and any("Save this access code" in s.value for s in at.success)
    labelled(at.button, "I have saved my code").click().run()
    assert not at.code

    labelled(at.text_input, "Skill or tool").set_value("SQL, Tableau")
    labelled(at.button, "Add to skills").click().run()
    labelled(at.text_input, "Job title").set_value("Data Analyst")
    labelled(at.text_input, "Employer").set_value("Fictional Grocers")
    labelled(at.text_area, "What you did (one accomplishment per line)").set_value("Built 12 Tableau dashboards\nWrote SQL queries")
    labelled(at.button, "Add to experience").click().run()
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs] == ["Skills (1)", "Education (0)", "Certifications (0)", "Experience (1)"]

    def draft_and_save(role, posting, label):
        at.switch_page(CANDIDATE_PAGES[1]).run()
        labelled(at.text_input, "Role you are applying for").set_value(role)
        labelled(at.text_area, "Job description").set_value(posting)
        at.button[0].click().run()
        assert not at.exception, at.exception
        assert [m.value for m in at.metric][0] == "Passed"
        body = next(m.value for m in at.markdown if m.value.startswith("# Avery Exampleton"))
        assert "**Data Analyst** · Fictional Grocers" in body and "Snowflake" not in body
        labelled(at.text_input, "Name this version").set_value(label)
        labelled(at.button, "Approve and save").click().run()
        assert any("Confirm that you have read" in e.value for e in at.error)  # approval is required
        at.checkbox[0].check()
        labelled(at.button, "Approve and save").click().run()
        assert not at.exception, at.exception
        assert any(f"Saved as '{label}'" in s.value for s in at.success)

    draft_and_save("BI Analyst", "Requirements\n- SQL queries\n- Snowflake", "BI")
    draft_and_save("Dashboard Developer", "Requirements\n- Tableau dashboards", "Dashboards")

    at.switch_page(CANDIDATE_PAGES[2]).run()
    assert not at.exception, at.exception
    assert list(at.dataframe[0].value["Version"]) == ["BI", "Dashboards"]
    assert any("line(s) only in A" in c.value for c in at.caption)  # the two versions order the bullets differently

    # A/B scoring and suggestions on the same page.
    labelled(at.text_area, "Job description").set_value(
        "Analyst\nRequirements:\n- Tableau dashboards for finance\n- Snowflake data warehouse experience\n"
        "- SQL queries to analyze sales\nNice to have:\n- Python with pandas\n- Forecasting models"
    )
    labelled(at.button, "Score these versions").click().run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    scores = next(d.value for d in at.dataframe if "Fit score" in d.value.columns)
    assert list(scores["Version"]) == ["BI", "Dashboards"] and all(float(v) > 0 for v in scores["Fit score"])
    assert any("equal weights" in c.value for c in at.caption)
    assert any("Snowflake data warehouse experience" in m.value and "Not in your core facts" in m.value for m in at.markdown)

    # Closing the profile and returning with the code brings everything back.
    at.switch_page(CANDIDATE_PAGES[0]).run()
    labelled(at.button, "Close my profile on this device").click().run()
    labelled(at.text_input, "Access code").set_value("WRONG-CODE-0000")
    labelled(at.button, "Open my profile").click().run()
    assert any("No profile matches" in e.value for e in at.error)
    labelled(at.text_input, "Access code").set_value(code.lower())
    labelled(at.button, "Open my profile").click().run()
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs][0] == "Skills (1)"

    # Delete everything.
    at.checkbox(key="delete_sure").check().run()
    labelled(at.button, "Delete my profile and all my data").click().run()
    assert not at.exception, at.exception
    with new_session(database) as session:
        for table in (CandidateProfile, CoreFact, ResumeVersion):
            assert session.exec(select(table)).all() == []
