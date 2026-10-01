"""A full screening run with the fake client: guardrails, evidence checks, caching, cost cap, determinism."""

import json

import pytest
from sqlmodel import select

from talentsift.audit import list_events
from talentsift.llm.base import LLMError
from talentsift.llm.fake_client import FakeLLMClient
from talentsift.models import (
    PARSE_NEEDS_OCR,
    PARSE_PARTIAL,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_STOPPED_COST_CAP,
    STATUS_AUTO_REJECTED,
    STATUS_NEEDS_REVIEW,
    STATUS_NOT_SHORTLISTED,
    STATUS_SHORTLISTED,
    CriterionScore,
    Evaluation,
)
from talentsift.scoring import ScreeningError, run_screening

from tests.factories import MEDIUM, STRONG, WEAK, make_applicant, make_role


def evaluations(session, run):
    rows = session.exec(select(Evaluation).where(Evaluation.run_id == run.id).order_by(Evaluation.applicant_id))
    return {e.applicant_id: e for e in rows}


def flags(evaluation):
    return [f["flag"] for f in json.loads(evaluation.flags_json)]


@pytest.fixture
def setup(session):
    role = make_role(session, threshold=40, top_n=1)
    applicants = [make_applicant(session, text) for text in (STRONG, MEDIUM, WEAK)]
    return role, applicants


def test_llm_only_sees_masked_text_and_label(session, settings, setup):
    role, applicants = setup
    client = FakeLLMClient()
    run_screening(session, role=role, applicants=applicants, client=client, settings=settings)
    assert len(client.calls) == 3
    for call, applicant in zip(sorted(client.calls, key=lambda c: c["user"]), sorted(applicants, key=lambda a: a.display_label)):
        prompt = call["system"] + call["user"]
        assert f'<resume label="{applicant.display_label}">' in prompt
        for secret in ("Avery", "Strongfit", "Blake", "Middleton", "Casey", "Unrelated", "@example.com", "555-0101"):
            assert secret not in prompt
        assert applicant.original_filename not in prompt


def test_fabricated_quote_goes_to_review_not_auto_reject(session, settings, setup):
    role, applicants = setup
    weak = applicants[2]

    def fabricate(reply, user_prompt):
        if f'label="{weak.display_label}"' in user_prompt:
            reply["criteria"][0].update(score=1, evidence=["Wrote SQL queries for a Fortune 500 bank"])
        return reply

    run = run_screening(session, role=role, applicants=applicants, client=FakeLLMClient(tamper=fabricate), settings=settings)
    weak_eval = evaluations(session, run)[weak.id]
    assert weak_eval.fit_score < role.auto_reject_threshold  # it would have been auto-rejected...
    assert weak_eval.status == STATUS_NEEDS_REVIEW  # ...but unverified evidence blocks that
    assert "evidence_unverified" in flags(weak_eval)
    assert list_events(session, run_id=run.id, event_types=["auto_reject"]) == []

    score = session.exec(select(CriterionScore).where(CriterionScore.evaluation_id == weak_eval.id, CriterionScore.score == 1)).one()
    assert score.evidence_verified is False
    assert json.loads(score.evidence_json) == [{"quote": "Wrote SQL queries for a Fortune 500 bank", "verified": False}]
    events = list_events(session, applicant_id=weak.id, event_types=["evidence_unverified"])
    assert len(events) == 1 and "Fortune 500" in events[0].payload_json


def test_fabricated_quote_also_keeps_a_strong_applicant_off_the_shortlist(session, settings, setup):
    role, applicants = setup
    strong = applicants[0]

    def fabricate(reply, user_prompt):
        if f'label="{strong.display_label}"' in user_prompt:
            reply["criteria"][1]["evidence"] = ["Managed a 40-person analytics department"]
        return reply

    run = run_screening(session, role=role, applicants=applicants, client=FakeLLMClient(tamper=fabricate), settings=settings)
    by_id = evaluations(session, run)
    assert by_id[strong.id].status == STATUS_NEEDS_REVIEW and by_id[strong.id].rank is None
    assert by_id[applicants[1].id].status == STATUS_SHORTLISTED  # the next verified applicant moves up


def test_validation_failure_after_retry_goes_to_review(session, settings, setup):
    role, applicants = setup
    client = FakeLLMClient(script=["{not json", {"criteria": []}])  # both attempts for the first applicant fail
    run = run_screening(session, role=role, applicants=applicants, client=client, settings=settings.with_overrides(llm_concurrency=1))
    first = evaluations(session, run)[applicants[0].id]
    assert first.status == STATUS_NEEDS_REVIEW and first.fit_score is None
    assert flags(first) == ["validation_failed"]
    assert len(list_events(session, applicant_id=applicants[0].id, event_types=["llm_request"])) == 2
    failed = list_events(session, applicant_id=applicants[0].id, event_types=["validation_failed"])
    assert len(failed) == 1
    assert "the reply was not a JSON object" in failed[0].payload_json
    assert "summary: Field required" in failed[0].payload_json


def test_one_bad_reply_is_retried_and_recovers(session, settings, setup):
    role, applicants = setup
    client = FakeLLMClient(script=["Sorry, here you go: nothing"])  # first attempt bad, retry uses offline answer
    run = run_screening(session, role=role, applicants=applicants, client=client, settings=settings.with_overrides(llm_concurrency=1))
    first = evaluations(session, run)[applicants[0].id]
    assert first.status == STATUS_SHORTLISTED
    assert run.llm_calls == 4  # 2 for the first applicant, 1 each for the others
    assert list_events(session, run_id=run.id, event_types=["validation_failed"]) == []


def test_partial_and_unparsed_resumes_are_never_auto_rejected(session, settings):
    role = make_role(session)
    partial = make_applicant(session, WEAK, parse_status=PARSE_PARTIAL, parse_message="Page 2 looks scanned.")
    scanned = make_applicant(session, "", parse_status=PARSE_NEEDS_OCR, parse_message="Needs OCR.")
    client = FakeLLMClient()
    run = run_screening(session, role=role, applicants=[partial, scanned], client=client, settings=settings)
    by_id = evaluations(session, run)
    assert by_id[partial.id].status == STATUS_NEEDS_REVIEW and flags(by_id[partial.id]) == ["partial_parse"]
    assert by_id[partial.id].fit_score is not None  # scored, but not decided automatically
    assert by_id[scanned.id].status == STATUS_NEEDS_REVIEW and flags(by_id[scanned.id]) == ["not_parsed"]
    assert len(client.calls) == 1  # the unparsed resume never reaches the model


def test_truncated_resume_goes_to_review(session, settings, setup):
    role, applicants = setup
    run = run_screening(session, role=role, applicants=applicants[2:], client=FakeLLMClient(), settings=settings.with_overrides(max_resume_chars=60))
    only = next(iter(evaluations(session, run).values()))
    assert only.status == STATUS_NEEDS_REVIEW and "input_truncated" in flags(only)


def test_cost_cap_stops_the_run_cleanly(session, settings, setup):
    role, applicants = setup
    capped = settings.with_overrides(cost_cap_usd=1.0, llm_concurrency=1)
    run = run_screening(session, role=role, applicants=applicants, client=FakeLLMClient(cost_per_call=0.6), settings=capped)
    assert run.status == RUN_STOPPED_COST_CAP
    assert run.total_cost == pytest.approx(1.2) and run.llm_calls == 2
    last = evaluations(session, run)[applicants[2].id]
    assert last.status == STATUS_NEEDS_REVIEW and flags(last) == ["not_scored"]
    assert "cost cap" in run.status_message


def test_fatal_provider_error_stops_calls_and_fails_the_run(session, settings, setup):
    role, applicants = setup
    client = FakeLLMClient(script=[LLMError("Invalid API key", fatal=True, status_code=401)])
    run = run_screening(session, role=role, applicants=applicants, client=client, settings=settings.with_overrides(llm_concurrency=1))
    assert run.status == RUN_FAILED and "Invalid API key" in run.status_message
    assert len(client.calls) == 1
    assert {e.status for e in evaluations(session, run).values()} == {STATUS_NEEDS_REVIEW}


def test_rerun_is_identical_and_served_from_cache(session, settings, setup):
    role, applicants = setup
    first = run_screening(session, role=role, applicants=applicants, client=FakeLLMClient(), settings=settings)
    client = FakeLLMClient()
    second = run_screening(session, role=role, applicants=applicants, client=client, settings=settings)
    forced = run_screening(session, role=role, applicants=applicants, client=FakeLLMClient(), settings=settings, force_rescore=True)

    def outcome(run):
        return [(a, e.fit_score, e.rank, e.status) for a, e in sorted(evaluations(session, run).items())]

    assert outcome(first) == outcome(second) == outcome(forced)
    assert second.cache_hits == 3 and second.llm_calls == 0 and client.calls == []
    assert forced.cache_hits == 0 and forced.llm_calls == 3
    cached_events = list_events(session, run_id=second.id, event_types=["llm_response"])
    assert all('"cached": true' in e.payload_json for e in cached_events)


def test_every_decision_traces_to_audit_events(session, settings, setup):
    role, applicants = setup
    run = run_screening(session, role=role, applicants=applicants, client=FakeLLMClient(), settings=settings)
    assert run.status == RUN_COMPLETED
    types = [e.event_type for e in list_events(session, run_id=run.id)]
    assert types[0] == "run_started" and types[-1] == "run_finished"
    assert types.count("llm_request") == types.count("llm_response") == types.count("score_computed") == 3
    assert types.count("ranking_computed") == 1 and types.count("auto_reject") == 1
    for applicant in applicants:
        per_applicant = {e.event_type for e in list_events(session, run_id=run.id, applicant_id=applicant.id)}
        assert {"llm_request", "llm_response", "score_computed"} <= per_applicant
    statuses = {e.status for e in evaluations(session, run).values()}
    assert statuses == {STATUS_SHORTLISTED, STATUS_NOT_SHORTLISTED, STATUS_AUTO_REJECTED}


def test_screening_requires_an_approved_rubric(session, settings):
    role = make_role(session, approve=False)
    applicant = make_applicant(session, STRONG)
    with pytest.raises(ScreeningError, match="not approved"):
        run_screening(session, role=role, applicants=[applicant], client=FakeLLMClient(), settings=settings)


def test_same_folder_two_roles_two_rankings(session, settings, setup):
    from talentsift.roles import CriterionInput

    analyst, applicants = setup
    retail = make_role(
        session,
        title="Retail Associate",
        criteria=[
            CriterionInput("Customer service", "Greeted customers and handled returns at the register", "must_have", "high"),
            CriterionInput("Stock management", "Restocked shelves and organized the stockroom", "nice_to_have", "medium"),
        ],
    )
    analyst_run = run_screening(session, role=analyst, applicants=applicants, client=FakeLLMClient(), settings=settings)
    retail_run = run_screening(session, role=retail, applicants=applicants, client=FakeLLMClient(), settings=settings)
    first = lambda run: min(evaluations(session, run).values(), key=lambda e: e.rank or 99).applicant_id
    assert first(analyst_run) == applicants[0].id
    assert first(retail_run) == applicants[2].id
