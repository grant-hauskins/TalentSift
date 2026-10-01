"""Schema validation of LLM output and the retry-once path, exercised with the fake client."""

import json

import pytest
from pydantic import ValidationError

from talentsift.llm.base import LLMError, call_with_validation, extract_json_object
from talentsift.llm.fake_client import FakeLLMClient, draft_rubric_offline, keywords, screen_offline
from talentsift.schemas import RubricDraft, ScreeningOutput, check_rubric_draft, check_screening_output

GOOD = {
    "criteria": [
        {"criterion_id": 1, "score": 3, "rationale": "Clear.", "evidence": ["Wrote SQL queries"]},
        {"criterion_id": 2, "score": 0, "rationale": "No evidence found.", "evidence": []},
    ],
    "strengths": ["SQL"],
    "gaps": ["Dashboards"],
    "summary": "The applicant writes SQL.",
}


def check_ids(output):
    check_screening_output(output, [1, 2])


@pytest.mark.parametrize(
    "bad",
    [
        {**GOOD, "criteria": [{**GOOD["criteria"][0], "score": 7}, GOOD["criteria"][1]]},  # out of range
        {**GOOD, "criteria": [{**GOOD["criteria"][0], "score": "high"}, GOOD["criteria"][1]]},  # wrong type
        {k: v for k, v in GOOD.items() if k != "summary"},  # missing key
        {**GOOD, "confidence": 0.9},  # extra key
    ],
)
def test_schema_rejects_malformed_output(bad):
    with pytest.raises(ValidationError):
        ScreeningOutput.model_validate(bad)


def test_semantic_check_rejects_missing_ids_and_unsupported_scores():
    output = ScreeningOutput.model_validate(
        {**GOOD, "criteria": [{"criterion_id": 1, "score": 2, "rationale": "Some.", "evidence": []}]}
    )
    with pytest.raises(ValueError) as raised:
        check_ids(output)
    assert "missing criterion_id(s) [2]" in str(raised.value)
    assert "no evidence quote" in str(raised.value)


def test_retry_path_recovers_from_one_malformed_reply():
    client = FakeLLMClient(script=["not json at all", GOOD])
    call = call_with_validation(client, "system", "user", ScreeningOutput, check=check_ids)
    assert call.ok
    assert len(call.attempts) == 2
    assert call.attempts[0].validation_error == "the reply was not a JSON object"
    # The retry tells the model what went wrong.
    assert "could not be used: the reply was not a JSON object" in client.calls[1]["user"]


def test_two_malformed_replies_fail_validation():
    bad = {**GOOD, "criteria": [GOOD["criteria"][0]]}  # criterion 2 missing both times
    client = FakeLLMClient(script=[bad, bad])
    call = call_with_validation(client, "system", "user", ScreeningOutput, check=check_ids)
    assert not call.ok
    assert len(call.attempts) == 2
    assert "missing criterion_id(s) [2]" in call.last_error


def test_call_error_stops_without_retry_and_reports_fatal():
    client = FakeLLMClient(script=[LLMError("No credits", fatal=True, status_code=402)])
    call = call_with_validation(client, "system", "user", ScreeningOutput)
    assert not call.ok
    assert call.fatal_error == "No credits"
    assert len(call.attempts) == 1


def test_extract_json_object_tolerates_wrapping():
    assert extract_json_object('Sure! {"a": 1} Hope this helps.') == {"a": 1}
    assert extract_json_object("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert extract_json_object("[1, 2]") is None
    assert extract_json_object("") is None


def screening_prompt(resume: str) -> str:
    criteria = [
        {"criterion_id": 7, "name": "SQL querying", "description": "Writes SQL queries", "type": "must_have"},
        {"criterion_id": 8, "name": "Forklift certification", "description": "Certified forklift operator", "type": "nice_to_have"},
    ]
    return f"<criteria>\n{json.dumps(criteria)}\n</criteria>\n\n<resume label=\"Applicant 01\">\n{resume}\n</resume>"


def test_fake_screening_quotes_resume_lines_verbatim():
    reply = screen_offline(screening_prompt("SUMMARY\n- Wrote SQL queries for weekly reports.\n- Hiked a lot."))
    output = ScreeningOutput.model_validate(reply)
    check_screening_output(output, [7, 8])
    sql, forklift = output.criteria
    assert sql.score >= 3 and sql.evidence == ["- Wrote SQL queries for weekly reports."]
    assert forklift.score == 0 and forklift.evidence == [] and forklift.rationale == "No evidence found."


def test_fake_client_is_deterministic():
    prompt = screening_prompt("Wrote SQL queries daily.")
    first = FakeLLMClient().complete_json("s", prompt, ScreeningOutput)
    second = FakeLLMClient().complete_json("s", prompt, ScreeningOutput)
    assert first.raw_text == second.raw_text


def test_keywords_are_stemmed_and_filtered():
    assert keywords("Experience building dashboards in Tableau") == ["build", "dashboard", "tableau"]
    assert keywords("Scheduling and schedules") == ["schedul"]


POSTING = """Business Data Analyst
Fictional Foods is hiring an analyst to turn sales data into decisions.

Responsibilities
- Build weekly reports

Requirements:
- 2+ years writing SQL queries to analyze sales data
- Build dashboards in Tableau or Power BI
- Present findings to non-technical stakeholders
- Advanced Excel, including pivot tables

Nice to have:
- Python (pandas) for data cleaning
- A/B testing and basic statistics
- Recent graduate preferred
"""


def test_fake_rubric_draft_follows_posting_and_flags_proxies():
    prompt = f"Role title from the manager (may be empty): \n\n<job_posting>\n{POSTING}\n</job_posting>"
    draft = RubricDraft.model_validate(draft_rubric_offline(prompt))
    check_rubric_draft(draft)
    assert draft.role_title == "Business Data Analyst"
    assert [c.type for c in draft.criteria].count("must_have") == 4
    assert draft.criteria[0].name == "Writing SQL queries to analyze sales data"
    flagged = [c for c in draft.criteria if c.proxy_risk]
    assert [c.description for c in flagged] == ["Recent graduate preferred"]
    assert "age" in flagged[0].proxy_note
