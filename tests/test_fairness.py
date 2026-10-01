"""Name-swap and consistency checks."""

from sqlmodel import select

from talentsift.audit import event_payload, list_events
from talentsift.fairness import consistency_check, find_name_swap_candidates, latest_run_with, name_swap_check
from talentsift.llm.fake_client import FakeLLMClient
from talentsift.models import Evaluation
from talentsift.scoring import run_screening

from tests.factories import MEDIUM, STRONG, WEAK, make_applicant, make_role

BODY = STRONG.split("\n", 1)[1]
EMILY = "Emily Sampleton | emily.sampleton@example.com | (614) 555-0110\n" + BODY.replace("Analyst who", "Emily is an analyst who")
JAMAL = "Jamal Testwood | jamal.testwood@example.com | (614) 555-0111\n" + BODY.replace("Analyst who", "Jamal is an analyst who")


def test_name_swap_pair_yields_identical_masked_text_and_status(session, settings):
    role = make_role(session, threshold=40, top_n=1)
    emily, jamal = make_applicant(session, EMILY), make_applicant(session, JAMAL)
    others = [make_applicant(session, text) for text in (MEDIUM, WEAK)]
    assert emily.masked_text == jamal.masked_text

    run = run_screening(session, role=role, applicants=[emily, jamal, *others], client=FakeLLMClient(), settings=settings)
    rows = {e.applicant_id: e for e in session.exec(select(Evaluation).where(Evaluation.run_id == run.id))}
    assert rows[emily.id].fit_score == rows[jamal.id].fit_score
    assert rows[emily.id].status == rows[jamal.id].status == "shortlisted"  # tie at top_n=1: both shortlisted

    report = name_swap_check(session, role=role, first=emily, second=jamal, client=FakeLLMClient(), settings=settings, run=run)
    assert report.passed
    assert report.masked_identical and report.scores_identical and report.status_identical
    assert report.run_statuses == ("shortlisted", "shortlisted")
    [event] = list_events(session, event_types=["fairness_check"])
    assert event_payload(event)["check"] == "name_swap" and event_payload(event)["passed"] is True
    assert find_name_swap_candidates(session) == [(emily, jamal)]
    assert latest_run_with(session, role, [emily.id, jamal.id]).id == run.id


def test_name_swap_detects_a_difference(session, settings):
    role = make_role(session)
    emily = make_applicant(session, EMILY)
    other = make_applicant(session, MEDIUM)
    report = name_swap_check(session, role=role, first=emily, second=other, client=FakeLLMClient(), settings=settings)
    assert not report.passed and not report.masked_identical and report.diff_preview


def test_consistency_check_passes_with_deterministic_client(session, settings):
    role = make_role(session, threshold=40, top_n=1)
    applicants = [make_applicant(session, text) for text in (STRONG, MEDIUM, WEAK)]
    run = run_screening(session, role=role, applicants=applicants, client=FakeLLMClient(), settings=settings)
    statuses_before = {e.id: e.status for e in session.exec(select(Evaluation))}

    client = FakeLLMClient()
    report = consistency_check(session, run, client, settings, sample_size=3)
    assert report.passed, report
    assert len(client.calls) == 3  # the cache was bypassed
    assert {e.id: e.status for e in session.exec(select(Evaluation))} == statuses_before  # nothing changed
    [event] = list_events(session, event_types=["fairness_check"])
    assert event_payload(event)["check"] == "consistency"


def test_consistency_check_flags_a_drifting_model(session, settings):
    role = make_role(session, threshold=40, top_n=1)
    applicants = [make_applicant(session, text) for text in (STRONG, MEDIUM, WEAK)]
    run = run_screening(session, role=role, applicants=applicants, client=FakeLLMClient(), settings=settings)

    def drift(reply, user_prompt):
        for item in reply["criteria"]:
            if item["score"] >= 2:
                item["score"] -= 1
        return reply

    report = consistency_check(session, run, FakeLLMClient(tamper=drift), settings, sample_size=3)
    assert not report.passed and not report.scores_identical
