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


# --- MVP success criteria and validation (T28, T29) ----------------------------------------------------

import re  # noqa: E402
import time  # noqa: E402

from talentsift.models import CATEGORIES, loads  # noqa: E402
from talentsift.scoring import screenable  # noqa: E402

ZIP = re.compile(r"\b430\d\d\b")  # every sample address uses a fictional 430xx ZIP code


def test_sow_success_criterion_25_resumes_ranked_with_a_breakdown_for_every_candidate(session, settings):
    """SOW section 2: rank a batch of at least 25 resumes in under five minutes, with a score breakdown each."""
    started = time.monotonic()
    summary = seed_demo(session, settings, screen=True)
    elapsed = time.monotonic() - started
    assert summary.applicants_added >= 25 and elapsed < 300

    for run in session.exec(select(ScreeningRun)):
        assert run.status == "completed" and run.applicants_total >= 25
        weights = loads(run.category_weights_json)
        assert set(weights) == set(CATEGORIES) and len(set(weights.values())) > 1  # the employer's own weights
        scored = session.exec(select(Evaluation).where(Evaluation.run_id == run.id, Evaluation.fit_score != None)).all()  # noqa: E711
        assert len(scored) >= 24
        ranks = sorted(e.rank for e in scored if e.rank is not None)
        assert ranks == list(range(1, len(ranks) + 1))  # a complete ranking, no gaps or ties in rank
        for evaluation in scored:
            breakdown = loads(evaluation.breakdown_json)
            used = [item for item in breakdown if item["score"] is not None]
            assert len(used) >= 3  # skills, experience, certifications (and education for the analyst role)
            assert all(item["weight"] == weights[item["category"]] for item in breakdown)


def test_repeat_scoring_gives_identical_rankings_even_without_the_cache(session, settings, demo):
    """SOW critical success factor: identical rankings when the same resumes are scored twice."""
    applicants = session.exec(select(Applicant).order_by(Applicant.id)).all()
    for role, first_run_id in zip(session.exec(select(Role).order_by(Role.id)).all(), demo.run_ids):
        again = run_screening(session, role=role, applicants=applicants, client=FakeLLMClient(), settings=settings, force_rescore=True)
        assert again.cache_hits == 0

        def ranking(run_id):
            rows = session.exec(select(Evaluation).where(Evaluation.run_id == run_id)).all()
            return sorted((e.applicant_id, e.fit_score, e.rank, e.status, e.breakdown_json) for e in rows)

        assert ranking(again.id) == ranking(first_run_id)


def test_no_name_street_address_or_zip_reaches_any_scoring_prompt(session, settings):
    """SOW critical success factor: names, street addresses, and ZIP codes removed before ranking."""
    client = FakeLLMClient()
    seed_demo(session, settings, client=client, screen=True)
    prompts = [call["user"] for call in client.calls if call["schema"] == "ScreeningOutput"]
    applicants = {a.display_label: a for a in session.exec(select(Applicant))}
    assert len(prompts) == 2 * len(screenable(list(applicants.values()))) >= 48

    for prompt in prompts:
        label = re.search(r'<resume label="([^"]+)">', prompt).group(1)
        raw_lines = applicants[label].raw_text.splitlines()
        name, street = raw_lines[0].split(), raw_lines[2].split(",")[0]  # "Avery Exampleton", "101 Example Street"
        resume = prompt.split("<resume", 1)[1]
        assert len(name) == 2 and re.match(r"\d+ \w+ \w+", street)
        for own in (*name, street):
            assert own not in resume, (label, own)
        assert not ZIP.search(resume), (label, ZIP.search(resume))
        assert "@example.com" not in resume and "555-01" not in resume
        assert "[NAME]" in resume and "[ADDRESS]" in resume
    all_names = {word for a in applicants.values() if a.raw_text for word in a.raw_text.splitlines()[0].split()}
    for event in list_events(session, event_types=["score_computed", "resume_ingested", "ranking_computed", "auto_reject"]):
        assert not ZIP.search(event.payload_json)
        assert not any(re.search(rf"\b{word}\b", event.payload_json) for word in all_names if len(word) > 6)


def test_candidate_flow_end_to_end_stays_separate_from_employer_data(session, settings, demo):
    """Profile -> draft -> guard -> approve -> save -> compare, next to a seeded employer database."""
    from talentsift.candidates import add_fact
    from talentsift.drafting import compare_versions, generate_draft, list_versions, save_version

    from tests.factories import ANALYST_POSTING, make_candidate

    employer_rows = (len(session.exec(select(Role)).all()), len(session.exec(select(Applicant)).all()), len(session.exec(select(Evaluation)).all()))
    profile, _code = make_candidate(session)
    first = save_version(
        session, profile,
        generate_draft(session, profile, FakeLLMClient(), settings, role_title="Business Data Analyst", role_text=ANALYST_POSTING),
        label="Analyst", approved=True,
    )
    add_fact(session, profile, category="skills", title="Power BI")
    second = save_version(
        session, profile,
        generate_draft(session, profile, FakeLLMClient(), settings, role_title="Reporting Analyst", role_text="Power BI and Excel reporting"),
        label="Reporting", approved=True,
    )
    assert [v.label for v in list_versions(session, profile.id)] == ["Analyst", "Reporting"]
    assert compare_versions(first, second).skills_only_right == ["Power BI"]
    for version in (first, second):
        assert loads(version.guard_json)["passed"] is True
        assert "Snowflake" not in version.body  # asked for by the posting, absent from the facts
    # Nothing on the employer side changed, and the draft never drew on employer data.
    assert employer_rows == (len(session.exec(select(Role)).all()), len(session.exec(select(Applicant)).all()), len(session.exec(select(Evaluation)).all()))
    assert "Fictional Foods" not in first.body
