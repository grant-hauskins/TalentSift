"""Candidate profiles and core facts (T20): consent, access codes, storage, and deletion."""

import pytest
from sqlmodel import select

from talentsift.audit import list_events
from talentsift.candidates import (
    CandidateError,
    add_fact,
    create_profile,
    delete_fact,
    delete_profile,
    find_profile,
    hash_access_code,
    list_facts,
    new_access_code,
    update_fact,
    update_profile,
)
from talentsift.models import CandidateProfile, CoreFact, ResumeVersion

from tests.factories import make_candidate


def test_a_profile_needs_consent_and_a_name(session):
    with pytest.raises(CandidateError, match="accept the notice"):
        create_profile(session, full_name="Avery Exampleton", consent=False)
    with pytest.raises(CandidateError, match="name"):
        create_profile(session, full_name="  ", consent=True)
    assert session.exec(select(CandidateProfile)).all() == []


def test_consent_is_recorded_and_the_code_opens_the_profile(session):
    profile, code = make_candidate(session, with_facts=False)
    assert profile.consent_at is not None and profile.consent_notice_version
    assert code not in profile.access_code_hash and len(profile.access_code_hash) == 64  # only a hash is stored
    assert find_profile(session, code).id == profile.id
    assert find_profile(session, code.lower().replace("-", " ")).id == profile.id  # typed loosely
    assert find_profile(session, new_access_code()) is None and find_profile(session, "") is None
    assert hash_access_code("abcd-efgh") == hash_access_code("ABCDEFGH")


def test_location_must_not_be_a_street_address_or_zip(session):
    with pytest.raises(CandidateError, match="city and state only"):
        create_profile(session, full_name="Avery Exampleton", consent=True, location="101 Example Street, Chicago, IL 60607")
    profile, _ = make_candidate(session, with_facts=False)
    with pytest.raises(CandidateError, match="city and state only"):
        update_profile(session, profile, full_name="Avery", email="", phone="", location="Chicago, IL 60607", headline="")


def test_facts_are_stored_by_category_edited_and_removed(session):
    profile, _ = make_candidate(session)
    facts = list_facts(session, profile.id)
    assert [f.category for f in facts] == ["skills", "education", "certifications", "experience", "experience"]
    job = next(f for f in facts if f.title == "Data Analyst")
    update_fact(session, job, title="Senior Data Analyst", organization=job.organization, start="2021", end="Present", details=" - Led reporting \n\n")
    assert job.title == "Senior Data Analyst" and job.end == "Present" and job.details == "- Led reporting"
    delete_fact(session, job)
    assert "Senior Data Analyst" not in [f.title for f in list_facts(session, profile.id)]

    with pytest.raises(CandidateError, match="job title"):
        add_fact(session, profile, category="experience", title=" ")
    with pytest.raises(CandidateError, match="Choose one of"):
        add_fact(session, profile, category="hobbies", title="Chess")


def test_delete_removes_everything_and_the_audit_log_never_held_personal_data(session):
    profile, code = make_candidate(session)
    session.add(ResumeVersion(profile_id=profile.id, label="v1", body="# Avery Exampleton"))
    session.commit()
    delete_profile(session, profile)
    assert find_profile(session, code) is None
    for table in (CandidateProfile, CoreFact, ResumeVersion):
        assert session.exec(select(table)).all() == []

    events = list_events(session)
    assert {e.event_type for e in events} == {"candidate_consent", "candidate_facts_changed", "candidate_data_deleted"}
    logged = " ".join(e.payload_json for e in events)
    for private in ("Avery", "Exampleton", "example.com", "555", "Fictional Grocers", "Tableau", "Chicago"):
        assert private not in logged
