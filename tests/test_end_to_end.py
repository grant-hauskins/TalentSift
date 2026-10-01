"""The definition of done, end to end, on the committed fictional samples with the offline client."""

import pytest
from sqlmodel import select

from talentsift.audit import list_events
from talentsift.demo import seed_demo
from talentsift.fairness import find_name_swap_candidates
from talentsift.llm.fake_client import FakeLLMClient
from talentsift.models import Applicant, Evaluation, Role, ScreeningRun
from talentsift.overrides import reinstate
from talentsift.roles import current_criteria
from talentsift.scoring import run_screening


@pytest.fixture
def demo(session, settings):
    summary = seed_demo(session, settings, screen=True)
    assert not summary.errors, summary.errors
    return summary


def outcome(session, run_id):
    """{filename prefix: (status, rank)} for a run."""
    rows = session.exec(select(Evaluation, Applicant).join(Applicant).where(Evaluation.run_id == run_id))
    return {applicant.original_filename[:2]: (evaluation.status, evaluation.rank) for evaluation, applicant in rows}


def shortlist(result):
    return sorted(prefix for prefix, (status, _) in result.items() if status == "shortlisted")


def test_postings_become_approved_rubrics_without_proxy_criteria(session, demo):
    roles = session.exec(select(Role).order_by(Role.id)).all()
    assert [r.title for r in roles] == ["Business Data Analyst", "Operations Coordinator"]
    assert all(r.is_approved for r in roles)
    assert demo.removed_proxy_criteria == ["Recent graduate preferred"]
    assert not any(c.proxy_warning for r in roles for c in current_criteria(session, r))
    assert len(list_events(session, event_types=["rubric_drafted"])) == 2
    assert len(list_events(session, event_types=["rubric_approved"])) == 2


def test_same_folder_two_roles_two_explainable_rankings(session, demo):
    analyst, operations = (outcome(session, run_id) for run_id in demo.run_ids)
    assert shortlist(analyst) == ["01", "02", "03", "17", "18"]
    assert shortlist(operations) == ["07", "08", "09"]
    for result in (analyst, operations):
        assert result["13"][0] == "auto_rejected"  # a barista is a weak fit for both
        assert result["19"][0] == result["20"][0] == "needs_review"  # scanned pages never auto-reject
    assert {p for p, (s, _) in analyst.items() if s == "not_shortlisted"} == {"04", "05", "06"}
    assert {p for p, (s, _) in operations.items() if s == "not_shortlisted"} == {"10", "11", "12"}
    for evaluation in session.exec(select(Evaluation).where(Evaluation.status != "shortlisted")):
        assert evaluation.reasons_json != "{}"


def test_scores_and_rejections_trace_to_verified_evidence(session, demo):
    for evaluation in session.exec(select(Evaluation).where(Evaluation.fit_score != None)):  # noqa: E711
        events = {e.event_type for e in list_events(session, run_id=evaluation.run_id, applicant_id=evaluation.applicant_id)}
        assert {"llm_request", "llm_response", "score_computed"} <= events
        if evaluation.status == "auto_rejected":
            assert "auto_reject" in events
    assert list_events(session, event_types=["evidence_unverified"]) == []  # the offline client quotes verbatim


def test_rerun_gives_same_ranking_and_statuses(session, settings, demo):
    applicants = session.exec(select(Applicant).order_by(Applicant.id)).all()
    for run_id in demo.run_ids:
        role = session.get(Role, session.get(ScreeningRun, run_id).role_id)
        again = run_screening(session, role=role, applicants=applicants, client=FakeLLMClient(), settings=settings)
        assert outcome(session, again.id) == outcome(session, run_id)
        assert again.llm_calls == 0  # everything came from the cache


def test_name_swap_pair_is_treated_identically(session, demo):
    [(first, second)] = find_name_swap_candidates(session)
    assert (first.original_filename, second.original_filename) == ("17_emily_sampleworth.pdf", "18_jamal_testerfield.pdf")
    for run_id in demo.run_ids:
        result = outcome(session, run_id)
        assert result["17"][0] == result["18"][0]


def test_auto_rejected_applicant_can_be_reinstated_with_logged_reason(session, demo):
    rejected = session.exec(select(Evaluation).where(Evaluation.status == "auto_rejected")).first()
    reinstate(session, rejected, "Transferable experience worth a phone screen")
    assert rejected.status == "not_shortlisted"
    [event] = list_events(session, event_types=["override"])
    assert "Transferable experience" in event.payload_json and event.applicant_id == rejected.applicant_id


def test_seeding_twice_adds_nothing(session, settings, demo):
    again = seed_demo(session, settings)
    assert again.roles_created == [] and again.applicants_added == 0
