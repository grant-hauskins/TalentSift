"""A/B scoring of candidate versions against a role, and improvement suggestions with nothing added."""

import json

import pytest
from sqlmodel import select

from talentsift.audit import list_events
from talentsift.candidates import CandidateError, add_fact, delete_fact, fact_views, list_facts
from talentsift.drafting import generate_draft, save_version
from talentsift.llm.base import LLMError
from talentsift.llm.fake_client import FakeLLMClient
from talentsift.models import Applicant, Criterion, Evaluation, Role, ScreeningRun
from talentsift.suggestions import GAP, INCLUDE, STRENGTHEN, suggest
from talentsift.version_scoring import resume_text_for_scoring, score_versions

from tests.factories import make_candidate

POSTING = """Business Data Analyst
Requirements:
- SQL queries to analyze sales data
- Dashboards in Tableau or Power BI
- Snowflake data warehouse experience
Nice to have:
- Train new analysts
- Bachelor's degree in business or analytics
- Recent graduate preferred
"""


def saved(session, settings, profile, label, role_text=POSTING):
    outcome = generate_draft(session, profile, FakeLLMClient(), settings, role_title="Business Data Analyst", role_text=role_text)
    return save_version(session, profile, outcome, label=label, approved=True)


@pytest.fixture
def two_versions(session, settings):
    """'Full' has every fact. 'Thin' was saved before the main job was added, so it lacks the dashboards and SQL work."""
    profile, _ = make_candidate(session)
    job = next(f for f in list_facts(session, profile.id) if f.title == "Data Analyst")
    details = job.details
    delete_fact(session, job)
    thin = saved(session, settings, profile, "Thin")
    add_fact(session, profile, category="experience", title="Data Analyst", organization="Fictional Grocers", start="2021", end="2025", details=details)
    full = saved(session, settings, profile, "Full")
    return profile, thin, full


def test_the_version_with_more_relevant_facts_scores_higher(session, settings, two_versions):
    profile, thin, full = two_versions
    result = score_versions(session, profile, [thin, full], FakeLLMClient(), settings, role_title="Business Data Analyst", role_text=POSTING)
    by_label = {s.version.label: s for s in result.scores}
    assert by_label["Full"].fit_score > by_label["Thin"].fit_score
    assert [s.version.label for s in result.best] == ["Full"]
    for score in result.scores:
        assert score.unverified == 0 and len(score.lines) == len(result.criteria)
        assert sum(part["points"] for part in score.fit.breakdown()) >= score.fit_score  # breakdown minus any penalty
    # One shared set of criteria, with the proxy criterion dropped.
    assert [c.name for c in result.criteria] == [line.criterion.name for line in result.scores[0].lines]
    assert not any("graduate" in c.name.lower() for c in result.criteria)
    assert {c.category for c in result.criteria} >= {"skills", "education"}


def test_scoring_is_repeatable(session, settings, two_versions):
    profile, thin, full = two_versions
    runs = [score_versions(session, profile, [thin, full], FakeLLMClient(), settings, role_text=POSTING) for _ in range(2)]
    assert [s.fit_score for s in runs[0].scores] == [s.fit_score for s in runs[1].scores]


def test_the_model_sees_no_name_or_contact_details_and_employer_data_is_untouched(session, settings, two_versions):
    profile, thin, full = two_versions
    client = FakeLLMClient()
    score_versions(session, profile, [thin, full], client, settings, role_text=POSTING)
    assert [call["schema"] for call in client.calls] == ["RubricDraft", "ScreeningOutput", "ScreeningOutput"]
    sent = " ".join(call["user"] for call in client.calls)
    for private in ("Avery", "Exampleton", "avery@example.com", "555-0101", "Chicago"):
        assert private not in sent
    assert "Avery" in full.body and "Avery" not in resume_text_for_scoring(full.body)
    for table in (Role, Criterion, Applicant, ScreeningRun, Evaluation):
        assert session.exec(select(table)).all() == []

    event = list_events(session, event_types=["versions_scored"])[-1]
    payload = json.loads(event.payload_json)
    assert payload["version_ids"] == [thin.id, full.id] and payload["criteria_count"] == 5
    for private in ("Avery", "Tableau", "Snowflake", "Fictional Grocers"):
        assert private not in event.payload_json


def test_evidence_not_found_in_the_version_is_flagged(session, settings, two_versions):
    profile, _thin, full = two_versions

    def fabricate(reply, _user):
        if "criteria" in reply and reply["criteria"] and "score" in reply["criteria"][0]:
            reply["criteria"][0]["evidence"] = ["Architected a Snowflake warehouse for 400 stores"]
        return reply

    result = score_versions(session, profile, [full], FakeLLMClient(tamper=fabricate), settings, role_text=POSTING)
    assert result.scores[0].unverified == 1 and result.scores[0].lines[0].verified is False


def test_clear_errors(session, settings, two_versions):
    profile, thin, full = two_versions
    with pytest.raises(CandidateError, match="at least one"):
        score_versions(session, profile, [], FakeLLMClient(), settings, role_text=POSTING)
    with pytest.raises(CandidateError, match="full job description"):
        score_versions(session, profile, [full], FakeLLMClient(), settings, role_text="Analyst")
    with pytest.raises(CandidateError, match="up to 4"):
        score_versions(session, profile, [full] * 5, FakeLLMClient(), settings, role_text=POSTING)
    other, _ = make_candidate(session, with_facts=False)
    with pytest.raises(CandidateError, match="your own versions"):
        score_versions(session, other, [full], FakeLLMClient(), settings, role_text=POSTING)
    with pytest.raises(CandidateError, match="could not read the job description"):
        score_versions(session, profile, [full], FakeLLMClient(script=[LLMError("down")]), settings, role_text=POSTING)
    # The rubric call works, one version's scoring call fails: that version is reported, the other still scores.
    client = FakeLLMClient()
    real = client.complete_json
    calls = []

    def flaky(system, user, schema):
        calls.append(schema.__name__)
        if calls.count("ScreeningOutput") == 1 and schema.__name__ == "ScreeningOutput":
            raise LLMError("timeout")
        return real(system, user, schema)

    client.complete_json = flaky
    result = score_versions(session, profile, [thin, full], client, settings, role_text=POSTING)
    assert result.scores[0].fit is None and "timeout" in result.scores[0].error
    assert [s.version.label for s in result.best] == ["Full"]


# --- Suggestions ---------------------------------------------------------------------------------------


def suggestions_for(session, settings, profile, version):
    result = score_versions(session, profile, [version], FakeLLMClient(), settings, role_text=POSTING)
    return suggest(result.scores[0], fact_views(list_facts(session, profile.id)))


def test_suggestions_point_to_the_candidates_own_facts_that_a_version_left_out(session, settings, two_versions):
    profile, thin, _full = two_versions
    found = {s.criterion: s for s in suggestions_for(session, settings, profile, thin)}
    dashboards = found["Dashboards in Tableau or Power BI"]
    assert dashboards.kind == INCLUDE and dashboards.must_have
    assert dashboards.fact_lines == ("Built 12 Tableau dashboards for the finance team (Data Analyst at Fictional Grocers)",)


def test_a_requirement_the_facts_do_not_cover_is_reported_as_a_gap_never_filled_in(session, settings, two_versions):
    profile, thin, full = two_versions
    for version in (thin, full):
        found = {s.criterion: s for s in suggestions_for(session, settings, profile, version)}
        snowflake = found["Snowflake data warehouse experience"]
        assert snowflake.kind == GAP and snowflake.fact_lines == ()
        assert "Nothing in your core facts shows this" in snowflake.text and "leave it out" in snowflake.text


def test_every_quoted_line_comes_verbatim_from_the_facts_and_must_haves_come_first(session, settings, two_versions):
    profile, thin, _full = two_versions
    facts = fact_views(list_facts(session, profile.id))
    everything = "\n".join(f.text for f in facts)
    suggestions = suggestions_for(session, settings, profile, thin)
    assert suggestions and all(s.score <= 2 for s in suggestions)
    for suggestion in suggestions:
        for line in suggestion.fact_lines:
            assert line.rsplit(" (", 1)[0] in everything or line.split(" (")[0] in everything.replace("\n", " ")
    flags = [s.must_have for s in suggestions]
    assert flags == sorted(flags, reverse=True)


def test_thin_evidence_already_in_the_version_asks_for_real_detail_not_new_claims(session, settings):
    profile, _ = make_candidate(session, with_facts=False)
    add_fact(session, profile, category="experience", title="Reporting Assistant", organization="Mock Markets", details="Wrote SQL reports")
    version = saved(session, settings, profile, "Only")
    found = {s.criterion: s for s in suggestions_for(session, settings, profile, version)}
    sql = found["SQL queries"]
    assert sql.kind == STRENGTHEN and sql.score <= 2
    assert sql.fact_lines == ("Wrote SQL reports (Reporting Assistant at Mock Markets)",)
    assert "add that detail to the core fact" in sql.text
    assert found["Dashboards in Tableau or Power BI"].kind == GAP
    assert all(s.kind != INCLUDE for s in found.values())  # the version already holds every fact there is


def test_an_unscored_version_gets_no_suggestions(session, settings, two_versions):
    profile, _thin, full = two_versions
    client = FakeLLMClient()
    real = client.complete_json
    client.complete_json = lambda system, user, schema: (_ for _ in ()).throw(LLMError("down")) if schema.__name__ == "ScreeningOutput" else real(system, user, schema)
    result = score_versions(session, profile, [full], client, settings, role_text=POSTING)
    assert result.best == [] and suggest(result.scores[0], []) == []
