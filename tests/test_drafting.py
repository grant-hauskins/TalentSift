"""Fact-anchored drafting (T21) and saved versions (T22): guard, retry, fallback, approval, compare."""

import json

import pytest
from sqlmodel import select

from talentsift.audit import list_events
from talentsift.candidates import CandidateError, add_fact, delete_fact, list_facts
from talentsift.drafting import compare_versions, delete_version, generate_draft, list_versions, save_version, version_stats
from talentsift.llm.base import LLMError
from talentsift.llm.fake_client import FakeLLMClient, draft_resume_offline
from talentsift.models import ResumeVersion

from tests.factories import ANALYST_POSTING, make_candidate


def invent(reply, _user):
    """A model that pads the resume with the posting's wish list."""
    reply["skills"].append("Snowflake")
    reply["experience"][0]["bullets"][0] = "Led a team of 8 building Snowflake dashboards with dbt, cutting costs 40%"
    reply["summary"] = "Certified senior analyst with stakeholder communication expertise."
    return reply


def draft_with(session, settings, profile, client=None, role_text=ANALYST_POSTING):
    return generate_draft(session, profile, client or FakeLLMClient(), settings, role_title="Business Data Analyst", role_text=role_text)


def test_draft_uses_only_the_facts_and_code_renders_titles_employers_and_dates(session, settings):
    profile, _ = make_candidate(session)
    outcome = draft_with(session, settings, profile)
    assert outcome.source == "ai" and outcome.report.passed and not outcome.blocked
    body = outcome.body
    assert body.startswith("# Avery Exampleton\nChicago, IL · avery@example.com · (312) 555-0101")
    assert "**Data Analyst** · Fictional Grocers · 2021 – 2025" in body
    assert "**B.S. Business Analytics** · Example State University · 2019" in body
    assert "**Tableau Desktop Specialist** · Tableau · 2022" in body
    for wished in ("Snowflake", "dbt", "Stakeholder", "Bachelor"):  # in the posting, not in the facts
        assert wished not in body


def test_the_model_never_receives_the_candidates_name_or_contact_details(session, settings):
    profile, _ = make_candidate(session)
    client = FakeLLMClient()
    draft_with(session, settings, profile, client)
    prompt = client.calls[0]["system"] + client.calls[0]["user"]
    for private in ("Avery", "Exampleton", "avery@example.com", "555-0101", "Chicago"):
        assert private not in prompt
    assert "Fictional Grocers" in prompt and "Snowflake" in prompt  # facts and posting are sent


def test_an_invented_draft_is_retried_then_replaced_by_the_candidates_own_wording(session, settings):
    profile, _ = make_candidate(session)
    client = FakeLLMClient(tamper=invent)
    outcome = draft_with(session, settings, profile, client)
    assert len(client.calls) == 2  # one retry, with the guard's feedback
    assert "not in the candidate's core facts" in client.calls[1]["user"]
    assert outcome.source == "template" and outcome.report.passed
    assert "added details that are not in your core facts" in outcome.message
    assert {v["kind"] for v in outcome.blocked} >= {"skill", "number", "named_term", "credential", "posting_term"}
    for invented in ("Snowflake", "dbt", "40%", "team of 8", "Certified", "senior"):
        assert invented not in outcome.body
    assert "Built 12 Tableau dashboards for the finance team" in outcome.body  # the candidate's own line
    assert outcome.model_used == ""  # the model's wording was not used

    blocked = list_events(session, event_types=["draft_guard_blocked"])[0]
    assert json.loads(blocked.payload_json)["fell_back_to_template"] is True


def test_a_clean_retry_is_accepted(session, settings):
    profile, _ = make_candidate(session)
    calls = []

    def invent_once(reply, user):
        calls.append(1)
        return invent(reply, user) if len(calls) == 1 else reply

    outcome = draft_with(session, settings, profile, FakeLLMClient(tamper=invent_once))
    assert outcome.source == "ai" and outcome.report.passed and outcome.blocked
    assert "rejected and redone" in outcome.message and "Snowflake" not in outcome.body


def test_ai_outage_falls_back_to_the_fact_template(session, settings):
    profile, _ = make_candidate(session)
    outcome = draft_with(session, settings, profile, FakeLLMClient(script=[LLMError("provider is down")]))
    assert outcome.source == "template" and outcome.report.passed and "not available" in outcome.message


def test_drafting_needs_facts(session, settings):
    profile, _ = make_candidate(session, with_facts=False)
    with pytest.raises(CandidateError, match="at least one core fact"):
        draft_with(session, settings, profile)


def test_audit_log_records_the_check_but_none_of_the_candidates_content(session, settings):
    profile, _ = make_candidate(session)
    outcome = draft_with(session, settings, profile, FakeLLMClient(tamper=invent))
    save_version(session, profile, outcome, label="Analyst", approved=True)
    events = list_events(session)
    assert {"resume_drafted", "draft_guard_blocked", "resume_version_saved"} <= {e.event_type for e in events}
    assert not {"llm_request", "llm_response"} & {e.event_type for e in events}  # prompts hold facts: not logged
    logged = " ".join(e.payload_json for e in events)
    for private in ("Avery", "Fictional Grocers", "Tableau", "dashboards", "Snowflake", "example.com"):
        assert private not in logged


def test_saving_needs_approval_a_unique_name_and_a_draft_that_still_matches_the_facts(session, settings):
    profile, _ = make_candidate(session)
    outcome = draft_with(session, settings, profile)
    with pytest.raises(CandidateError, match="Confirm that you have read"):
        save_version(session, profile, outcome, label="Analyst", approved=False)
    with pytest.raises(CandidateError, match="name"):
        save_version(session, profile, outcome, label="  ", approved=True)
    version = save_version(session, profile, outcome, label="Analyst", approved=True)
    assert version.source == "ai" and json.loads(version.guard_json)["passed"] is True
    assert version.facts_hash == outcome.report.facts_hash and version.body == outcome.body
    with pytest.raises(CandidateError, match="already have a version named"):
        save_version(session, profile, outcome, label="analyst", approved=True)

    # The candidate removes a job after drafting: the old draft cites a fact that is gone.
    job = next(f for f in list_facts(session, profile.id) if f.title == "Data Analyst")
    delete_fact(session, job)
    with pytest.raises(CandidateError, match="no longer matches"):
        save_version(session, profile, outcome, label="Stale", approved=True)
    assert [v.label for v in list_versions(session, profile.id)] == ["Analyst"]


def test_versions_are_listed_compared_and_deleted(session, settings):
    profile, _ = make_candidate(session)
    first = save_version(session, profile, draft_with(session, settings, profile), label="Analyst", approved=True)
    add_fact(session, profile, category="skills", title="Power BI")
    ops = generate_draft(session, profile, FakeLLMClient(), settings, role_title="Reporting Lead", role_text="Excel vendor files Power BI")
    second = save_version(session, profile, ops, label="Reporting", approved=True)

    stats = [version_stats(v) for v in list_versions(session, profile.id)]
    assert [s["Version"] for s in stats] == ["Analyst", "Reporting"]
    assert stats[0]["Skills"] == 4 and stats[1]["Skills"] == 5 and stats[0]["Bullets"] == 4

    comparison = compare_versions(first, second)
    assert not comparison.identical and not comparison.same_facts
    assert comparison.skills_only_right == ["Power BI"] and comparison.skills_only_left == []
    assert comparison.lines_only_left > 0 and comparison.lines_only_right > 0
    assert any(line.startswith("+") and "Power BI" in line for line in comparison.diff)
    assert compare_versions(first, first).identical

    delete_version(session, first)
    assert [v.label for v in session.exec(select(ResumeVersion))] == ["Reporting"]


def test_offline_client_reads_facts_from_the_prompt(session, settings):
    profile, _ = make_candidate(session)
    client = FakeLLMClient()
    draft_with(session, settings, profile, client)
    reply = draft_resume_offline(client.calls[0]["user"])
    assert reply["skills"][:2] == ["SQL", "Tableau"]  # the posting asks for these first
    assert reply["summary"].startswith("Business data analyst.")
