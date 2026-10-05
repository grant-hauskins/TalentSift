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
        assert seed_counts(session) == (2, 20)

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
