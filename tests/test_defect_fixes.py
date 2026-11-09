"""Regression tests for the defects logged in docs/prototype_audit.md (T13)."""

import pytest
from sqlmodel import select

from talentsift.config import Settings
from talentsift.fairness import FairnessCheckError, consistency_check
from talentsift.ingest import REJECTED, ingest_upload
from talentsift.job_import import JobImportError, check_public_host, extract_posting, normalize_url
from talentsift.llm.fake_client import FakeLLMClient
from talentsift.models import Applicant, Evaluation, loads
from talentsift.scoring import FLAG_NAME_NOT_MASKED, run_screening

from tests.factories import MEDIUM, STRONG, WEAK, make_applicant, make_role

NO_NAME = "SUMMARY\nAnalyst who writes SQL queries to analyze data.\n- Built dashboards in Tableau and Power BI."


def public(_host):
    return ["93.184.216.34"]


# --- D-01: a resume whose name was not masked never reaches the model ---------------------------------


def test_resume_without_a_masked_name_is_not_sent_to_the_model(session, settings):
    role = make_role(session)
    named, unnamed = make_applicant(session, STRONG), make_applicant(session, NO_NAME)
    client = FakeLLMClient()
    run = run_screening(session, role=role, applicants=[named, unnamed], client=client, settings=settings)

    assert len(client.calls) == 1 and "writes SQL queries to analyze data every day" in client.calls[0]["user"]
    evaluation = session.exec(select(Evaluation).where(Evaluation.run_id == run.id, Evaluation.applicant_id == unnamed.id)).one()
    assert evaluation.status == "needs_review" and evaluation.fit_score is None
    assert [flag["flag"] for flag in loads(evaluation.flags_json)] == [FLAG_NAME_NOT_MASKED]


def test_name_mask_requirement_can_be_turned_off_for_anonymous_text(session, settings):
    role = make_role(session)
    client = FakeLLMClient()
    run_screening(session, role=role, applicants=[make_applicant(session, NO_NAME)], client=client,
                  settings=settings.with_overrides(require_name_mask=False))
    assert len(client.calls) == 1


# --- D-07: unreadable uploads do not become applicants -------------------------------------------------


@pytest.mark.parametrize(
    "filename, data", [("empty.pdf", b""), ("notes.pdf", b"hello world"), ("resume.docx", b"%PDF-1.4"), ("resume", b"%PDF-1.4")]
)
def test_bad_uploads_are_rejected_without_creating_an_applicant(session, settings, tmp_path, filename, data):
    settings = settings.with_overrides(upload_dir=tmp_path / "uploads")
    result = ingest_upload(session, filename, data, batch="b", settings=settings)
    assert result.outcome == REJECTED and result.applicant is None and "Not imported" in result.message
    assert session.exec(select(Applicant)).all() == []
    assert not (tmp_path / "uploads").exists() or not list((tmp_path / "uploads").iterdir())


# --- D-08: the consistency check compares like with like ----------------------------------------------


def test_consistency_check_uses_the_runs_own_penalty_and_model(session, settings):
    role = make_role(session, threshold=40, top_n=1)
    applicants = [make_applicant(session, text) for text in (STRONG, MEDIUM, WEAK)]
    run = run_screening(session, role=role, applicants=applicants, client=FakeLLMClient(), settings=settings)

    changed = settings.with_overrides(must_have_penalty=0.0)  # the setting changed after the run
    report = consistency_check(session, run, FakeLLMClient(), changed)
    assert report.passed

    with pytest.raises(FairnessCheckError, match="Switch back to the run's model"):
        consistency_check(session, run, FakeLLMClient(model="fake/other-model"), settings)


# --- D-09: malformed job links give a message, not a traceback ----------------------------------------


@pytest.mark.parametrize("url", ["https://" + "a" * 70 + ".com/job", "https://a..b.com/job"])
def test_malformed_hosts_are_refused_cleanly(url):
    def idna(host):
        host.encode("idna")
        return ["93.184.216.34"]

    with pytest.raises(JobImportError, match="Could not find the website"):
        check_public_host(url, idna)


def test_invalid_port_and_nat64_addresses_are_refused():
    with pytest.raises(JobImportError, match="invalid port"):
        normalize_url("https://example.com:abc/job")
    with pytest.raises(JobImportError, match="private or local"):
        check_public_host("https://example.com/job", lambda _h: ["64:ff9b::7f00:1"])
    check_public_host("https://example.com/job", public)  # a normal public address still passes


def test_json_ld_with_an_unexpected_address_shape_does_not_crash():
    html = (
        '<html><body><script type="application/ld+json">{"@type":"JobPosting","title":"Analyst",'
        '"description":"' + "Analyze sales data and build dashboards for store leaders. " * 6 + '",'
        '"jobLocation":{"address":["a","b"]}}</script></body></html>'
    )
    posting = extract_posting(html, url="https://example.com/job")
    assert posting.title == "Analyst"


# --- D-10 / T14: shared-install settings ---------------------------------------------------------------


def test_folder_import_can_be_limited_or_turned_off(tmp_path):
    settings = Settings.from_env({"ALLOW_FOLDER_IMPORT": "false", "IMPORT_ROOT": str(tmp_path)})
    assert settings.allow_folder_import is False and settings.import_root == tmp_path
    defaults = Settings.from_env({})
    assert defaults.allow_folder_import is True and defaults.import_root is None and defaults.require_name_mask is True
