"""Status rules: threshold, top N, ties, guardrails, and both AUTO_REJECT_MODE settings."""

import json

import pytest
from sqlmodel import select

from talentsift.audit import list_events
from talentsift.llm.fake_client import FakeLLMClient
from talentsift.models import (
    STATUS_AUTO_REJECTED,
    STATUS_NEEDS_REVIEW,
    STATUS_NOT_SHORTLISTED,
    STATUS_PENDING_AUTO_REJECT,
    STATUS_SHORTLISTED,
    Evaluation,
)
from talentsift.overrides import confirm_pending_rejections
from talentsift.scoring import RankInput, assign_statuses, run_screening

from tests.factories import MEDIUM, STRONG, WEAK, make_applicant, make_role


def statuses(ranking):
    return {applicant_id: decision.status for applicant_id, decision in ranking.decisions.items()}


def test_top_n_threshold_and_middle():
    items = [RankInput(i, score, 3) for i, score in enumerate([90, 80, 70, 60, 50, 39.9, 10], start=1)]
    ranking = assign_statuses(items, threshold=40, top_n=2, mode="automatic")
    assert statuses(ranking) == {
        1: STATUS_SHORTLISTED,
        2: STATUS_SHORTLISTED,
        3: STATUS_NOT_SHORTLISTED,
        4: STATUS_NOT_SHORTLISTED,
        5: STATUS_NOT_SHORTLISTED,
        6: STATUS_AUTO_REJECTED,
        7: STATUS_AUTO_REJECTED,
    }
    assert [ranking.decisions[i].rank for i in range(1, 8)] == [1, 2, 3, 4, 5, 6, 7]


def test_exactly_at_threshold_is_not_rejected():
    ranking = assign_statuses([RankInput(1, 40.0, 0)], threshold=40, top_n=0, mode="automatic")
    assert ranking.decisions[1].status == STATUS_NOT_SHORTLISTED


def test_threshold_beats_top_n():
    # Only three applicants, all below the threshold: nobody is shortlisted just to fill the top 5.
    items = [RankInput(1, 35, 1), RankInput(2, 30, 1), RankInput(3, 20, 0)]
    ranking = assign_statuses(items, threshold=40, top_n=5, mode="automatic")
    assert set(statuses(ranking).values()) == {STATUS_AUTO_REJECTED}


def test_ranking_order_uses_must_haves_then_applicant_id():
    items = [RankInput(9, 70, 1), RankInput(4, 70, 3), RankInput(2, 70, 3), RankInput(1, 90, 0)]
    ranking = assign_statuses(items, threshold=0, top_n=10, mode="automatic")
    assert ranking.order == [1, 2, 4, 9]


def test_ties_at_the_cutoff_are_all_shortlisted():
    items = [RankInput(1, 90, 3), RankInput(2, 75, 2), RankInput(3, 75, 2), RankInput(4, 75, 1)]
    ranking = assign_statuses(items, threshold=40, top_n=2, mode="automatic")
    assert statuses(ranking) == {
        1: STATUS_SHORTLISTED,
        2: STATUS_SHORTLISTED,
        3: STATUS_SHORTLISTED,  # same fit and must-haves as the 2nd: not decided by upload order
        4: STATUS_NOT_SHORTLISTED,  # same fit but fewer must-haves met
    }


@pytest.mark.parametrize(
    "flag", ["validation_failed", "evidence_unverified", "partial_parse", "input_truncated", "not_scored", "llm_error"]
)
def test_guardrail_flags_never_auto_reject(flag):
    items = [RankInput(1, 5.0, 0, (flag,)), RankInput(2, 95.0, 3, (flag,)), RankInput(3, 10.0, 0)]
    ranking = assign_statuses(items, threshold=40, top_n=5, mode="automatic")
    assert ranking.decisions[1].status == STATUS_NEEDS_REVIEW and ranking.decisions[1].rank is None
    assert ranking.decisions[2].status == STATUS_NEEDS_REVIEW  # flagged applicants are never ranked
    assert ranking.decisions[3].status == STATUS_AUTO_REJECTED


def test_unscored_applicant_goes_to_review():
    ranking = assign_statuses([RankInput(1, None, 0)], threshold=40, top_n=5, mode="automatic")
    assert ranking.decisions[1].status == STATUS_NEEDS_REVIEW


def test_confirm_mode_proposes_instead_of_rejecting():
    items = [RankInput(1, 80, 2), RankInput(2, 20, 0)]
    ranking = assign_statuses(items, threshold=40, top_n=5, mode="confirm")
    assert statuses(ranking) == {1: STATUS_SHORTLISTED, 2: STATUS_PENDING_AUTO_REJECT}


def _screen(session, settings, mode):
    role = make_role(session, threshold=40, top_n=1)
    applicants = [make_applicant(session, text) for text in (STRONG, MEDIUM, WEAK)]
    run = run_screening(
        session,
        role=role,
        applicants=applicants,
        client=FakeLLMClient(),
        settings=settings.with_overrides(auto_reject_mode=mode),
    )
    by_applicant = {e.applicant_id: e for e in session.exec(select(Evaluation).where(Evaluation.run_id == run.id))}
    return run, [by_applicant[a.id] for a in applicants]


def test_automatic_mode_end_to_end(session, settings):
    run, (strong, medium, weak) = _screen(session, settings, "automatic")
    assert (strong.status, medium.status, weak.status) == (STATUS_SHORTLISTED, STATUS_NOT_SHORTLISTED, STATUS_AUTO_REJECTED)
    assert strong.fit_score > medium.fit_score > weak.fit_score
    events = list_events(session, run_id=run.id, event_types=["auto_reject"])
    assert len(events) == 1 and events[0].applicant_id == weak.applicant_id
    payload = events[0].payload_json
    assert '"threshold": 40' in payload and '"fit_score": ' in payload and '"reasons": ' in payload


def test_confirm_mode_end_to_end(session, settings):
    run, (strong, medium, weak) = _screen(session, settings, "confirm")
    assert weak.status == STATUS_PENDING_AUTO_REJECT
    assert list_events(session, run_id=run.id, event_types=["auto_reject"]) == []

    assert confirm_pending_rejections(session, run, note="Reviewed the batch") == 1
    session.refresh(weak)
    assert weak.status == STATUS_AUTO_REJECTED and weak.auto_status == STATUS_PENDING_AUTO_REJECT
    events = list_events(session, run_id=run.id, event_types=["auto_reject"])
    assert len(events) == 1 and '"mode": "confirm"' in events[0].payload_json


def test_reasons_are_specific_and_job_related(session, settings):
    _, (strong, medium, weak) = _screen(session, settings, "automatic")
    reasons = json.loads(weak.reasons_json)
    assert reasons["headline"] == "Below the minimum fit score for this role"
    assert "below this role's minimum of 40" in reasons["explanation"]
    assert {r["criterion"] for r in reasons["unmet_must_haves"]} == {"SQL querying", "Dashboards", "Stakeholder communication"}
    medium_reasons = json.loads(medium.reasons_json)
    assert "meets this role's minimum of 40" in medium_reasons["explanation"]
    assert medium_reasons["unmet_must_haves"] == []  # all must-haves met; ranked below the shortlist
    assert [r["criterion"] for r in medium_reasons["weakest_criteria"]][:2] == ["Experimentation", "Python"]
    assert "Areas with the least evidence: Experimentation; Python." in medium_reasons["explanation"]
    for text in (reasons["explanation"], medium_reasons["explanation"]):
        for word in ("age", "gender", "name", "young", "old"):
            assert f" {word} " not in f" {text.lower()} "
