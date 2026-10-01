"""Rubric drafting from a posting, editing, versioning, approval, and duplication."""

import pytest

from talentsift.audit import event_payload, list_events
from talentsift.llm.base import LLMError
from talentsift.llm.fake_client import FakeLLMClient
from talentsift.roles import (
    CriterionInput,
    RoleError,
    approve_role,
    criteria_for_version,
    current_criteria,
    discard_draft,
    duplicate_role,
    save_role,
    version_history,
)
from talentsift.rubric import draft_rubric, redraft_rubric

from tests.test_llm_validation import POSTING


def draft(session, settings, **kwargs):
    outcome = draft_rubric(session, FakeLLMClient(), settings, posting_text=POSTING, **kwargs)
    assert outcome.ok, outcome.error
    return outcome


def edit(session, role, criteria, **changes):
    values = dict(
        title=role.title,
        summary=role.summary,
        posting_text=role.posting_text,
        criteria=criteria,
        auto_reject_threshold=role.auto_reject_threshold,
        top_n=role.top_n,
    )
    values.update(changes)
    return save_role(session, role, **values)


def rows(session, role):
    return [CriterionInput.from_criterion(c) for c in current_criteria(session, role)]


def test_posting_becomes_a_draft_rubric(session, settings):
    outcome = draft(session, settings)
    role = outcome.role
    criteria = current_criteria(session, role)
    assert role.title == "Business Data Analyst" and role.version == 1 and not role.is_approved
    assert 5 <= len(criteria) <= 10
    assert all(c.source == "ai_draft" for c in criteria)
    assert outcome.proxy_flags == ["Recent graduate preferred"]
    flagged = [c for c in criteria if c.proxy_warning]
    assert len(flagged) == 1 and "age" in flagged[0].proxy_warning
    [event] = list_events(session, role_id=role.id, event_types=["rubric_drafted"])
    assert event_payload(event)["proxy_flags"] == ["Recent graduate preferred"]
    assert len(list_events(session, role_id=role.id, event_types=["llm_request"])) == 1


def test_manager_title_wins_over_ai_title(session, settings):
    assert draft(session, settings, title="Data Analyst II").role.title == "Data Analyst II"


def test_failed_draft_reports_error_and_creates_no_role(session, settings):
    client = FakeLLMClient(script=["nope", "still nope"])
    outcome = draft_rubric(session, client, settings, posting_text=POSTING)
    assert not outcome.ok and "could not draft" in outcome.error
    assert list_events(session, event_types=["validation_failed"])
    fatal = draft_rubric(session, FakeLLMClient(script=[LLMError("bad key", fatal=True)]), settings, posting_text=POSTING)
    assert "bad key" in fatal.error


def test_short_posting_is_refused(session, settings):
    assert "Paste the full job posting" in draft_rubric(session, FakeLLMClient(), settings, posting_text="Analyst").error


def test_editing_a_draft_updates_in_place_and_marks_manager_rows(session, settings):
    role = draft(session, settings).role
    criteria = rows(session, role)
    criteria[0].weight = "medium"
    criteria = [c for c in criteria if not c.proxy_warning]  # drop the flagged criterion
    criteria.append(CriterionInput("SQL certification", "Holds a SQL certification", "nice_to_have", "low"))
    outcome = edit(session, role, criteria)
    assert outcome.action == "draft_updated" and role.version == 1
    stored = current_criteria(session, role)
    assert [c.source for c in stored][0] == "manager" and stored[-1].source == "manager"
    assert stored[1].source == "ai_draft"  # untouched rows keep their source
    assert not any(c.proxy_warning for c in stored)


def test_approved_rubric_is_immutable_and_edits_create_a_new_version(session, settings):
    role = draft(session, settings).role
    approve_role(session, role)
    v1_ids = [c.id for c in current_criteria(session, role)]
    criteria = rows(session, role)
    criteria[0].description += " in PostgreSQL"
    outcome = edit(session, role, criteria)
    assert outcome.action == "new_version" and role.version == 2 and not role.is_approved
    assert [c.id for c in criteria_for_version(session, role.id, 1)] == v1_ids  # v1 untouched
    assert {v["status"] for v in version_history(session, role)} == {"approved", "draft"}
    approve_role(session, role)
    assert role.is_approved and role.approved_version == 2
    [_, approval] = list_events(session, role_id=role.id, event_types=["rubric_approved"])
    assert event_payload(approval)["snapshot"]["criteria"][0]["description"].endswith("in PostgreSQL")


def test_threshold_change_does_not_create_a_version(session, settings):
    role = draft(session, settings).role
    approve_role(session, role)
    outcome = edit(session, role, rows(session, role), auto_reject_threshold=55, top_n=3)
    assert outcome.action == "policy_updated"
    assert (role.version, role.is_approved, role.auto_reject_threshold, role.top_n) == (1, True, 55, 3)
    assert edit(session, role, rows(session, role), auto_reject_threshold=55, top_n=3).action == "no_change"


def test_discard_draft_returns_to_approved_version(session, settings):
    role = draft(session, settings).role
    approve_role(session, role)
    original_title = role.title
    edit(session, role, rows(session, role), title="Renamed role")
    assert role.version == 2
    discard_draft(session, role)
    assert role.version == 1 and role.is_approved and role.title == original_title


def test_invalid_rubrics_are_refused(session, settings):
    role = draft(session, settings).role
    with pytest.raises(RoleError, match="at least one"):
        edit(session, role, [])
    duplicate_names = [CriterionInput("SQL", "a"), CriterionInput("sql", "b")]
    with pytest.raises(RoleError, match="unique"):
        edit(session, role, duplicate_names)
    with pytest.raises(RoleError, match="threshold"):
        edit(session, role, rows(session, role), auto_reject_threshold=120)
    with pytest.raises(RoleError, match="already approved"):
        approve_role(session, role) or approve_role(session, role)


def test_duplicate_role_copies_rubric_as_new_draft(session, settings):
    role = draft(session, settings).role
    approve_role(session, role)
    copy = duplicate_role(session, role)
    assert copy.id != role.id and copy.title.endswith("(copy)") and not copy.is_approved
    assert [c.name for c in current_criteria(session, copy)] == [c.name for c in current_criteria(session, role)]
    [event] = list_events(session, role_id=copy.id, event_types=["role_saved"])
    assert event_payload(event)["action"] == "duplicated"


def test_redraft_on_approved_role_creates_ai_draft_version(session, settings):
    role = draft(session, settings).role
    criteria = rows(session, role)[:5]
    edit(session, role, criteria)
    approve_role(session, role)
    outcome = redraft_rubric(session, role, FakeLLMClient(), settings)
    assert outcome.ok and role.version == 2 and not role.is_approved
    assert all(c.source == "ai_draft" for c in current_criteria(session, role))
