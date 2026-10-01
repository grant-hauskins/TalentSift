"""Headless Streamlit tests: every page renders, the banner is everywhere, and key flows work end to end."""

from pathlib import Path

import pytest
from sqlmodel import select
from streamlit.testing.v1 import AppTest

from talentsift.config import get_settings
from talentsift.db import create_db_engine, init_db, new_session
from talentsift.models import STATUS_AUTO_REJECTED, Evaluation
from talentsift.ui import BANNER

from tests.factories import MEDIUM, STRONG, WEAK, make_applicant, make_role

APP = str(Path(__file__).resolve().parent.parent / "app.py")
PAGES = ["pages/1_Roles.py", "pages/2_Applicants.py", "pages/3_Screen.py", "pages/4_Results.py", "pages/5_Audit.py"]


@pytest.fixture
def database(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'ui.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("AUTO_REJECT_MODE", "automatic")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
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
