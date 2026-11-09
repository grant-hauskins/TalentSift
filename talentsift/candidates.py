"""Candidate profiles and core facts: the candidate's own, verified statements about their background.

There are no user accounts in the MVP (SOW: accounts are on the production roadmap). A candidate reaches
their profile with an access code that is shown once at creation; only its SHA-256 hash is stored.

Privacy rules for this module:
* A profile cannot be created without accepting the AI notice (`CONSENT_NOTICE`).
* Nothing here reads employer tables (roles, applicants, runs), and nothing links a profile to them.
* Audit events carry the profile id, counts, and hashes only: never names, facts, or draft text.
* `delete_profile` removes the profile, its facts, and its saved versions for good.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import Sequence

from sqlmodel import Session, select

from talentsift.audit import log_event
from talentsift.draft_template import FactView
from talentsift.models import FACT_CATEGORIES, CandidateProfile, CoreFact, ResumeVersion, utcnow

NOTICE_VERSION = "2026-11-v1"
CONSENT_NOTICE = """\
**Before you start: how TalentSift uses AI and your information**

- **AI drafting.** When you ask for a draft, the facts you enter (skills, education, certifications,
  experience) and the job description you paste are sent to an AI model to suggest wording. Your name,
  email, phone, and location are not sent to the model.
- **Nothing is added.** Every draft is checked by software against your facts. A draft that adds a skill,
  credential, employer, date, or number you did not enter is rejected. You approve every draft before it is
  saved.
- **AI screening by employers.** Employers that use TalentSift score resumes with AI against the role's
  criteria. Names, street addresses, and ZIP codes are removed before scoring, and a person reviews every
  shortlist. Employers remain responsible for their own legally required notices, including those under
  Illinois Public Act 103-0804.
- **Your data.** Your profile is separate from employer data. Employers cannot see it, and you cannot see
  theirs. You can delete your profile, facts, and saved versions at any time from this page.
"""

_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
MAX_FACTS = 60
MAX_FIELD_CHARS = 200
MAX_DETAILS_CHARS = 4000


class CandidateError(ValueError):
    """A profile or fact change that cannot be saved; the message is shown to the candidate."""


# --- Access codes ------------------------------------------------------------------------------------


def new_access_code() -> str:
    """A random code such as "K7QM-2XHP-9RTB" (about 59 bits)."""
    return "-".join("".join(secrets.choice(_CODE_ALPHABET) for _ in range(4)) for _ in range(3))


def hash_access_code(code: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]", "", code.upper())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# --- Profiles ----------------------------------------------------------------------------------------


def _clean_location(location: str) -> str:
    location = location.strip()
    if re.search(r"\d", location):
        raise CandidateError("Enter your city and state only: no street address or ZIP code.")
    return location


def create_profile(
    session: Session,
    *,
    full_name: str,
    consent: bool,
    email: str = "",
    phone: str = "",
    location: str = "",
    headline: str = "",
) -> tuple[CandidateProfile, str]:
    """Create a profile after the candidate accepts the notice. Returns the profile and its one-time code."""
    if not consent:
        raise CandidateError("Please read and accept the notice before creating a profile.")
    if not full_name.strip():
        raise CandidateError("Enter your name as it should appear on your resume.")
    code = new_access_code()
    now = utcnow()
    profile = CandidateProfile(
        access_code_hash=hash_access_code(code),
        full_name=full_name.strip()[:MAX_FIELD_CHARS],
        email=email.strip()[:MAX_FIELD_CHARS],
        phone=phone.strip()[:MAX_FIELD_CHARS],
        location=_clean_location(location)[:MAX_FIELD_CHARS],
        headline=headline.strip()[:MAX_FIELD_CHARS],
        consent_at=now,
        consent_notice_version=NOTICE_VERSION,
    )
    session.add(profile)
    session.flush()
    log_event(session, "candidate_consent", payload={"profile_id": profile.id, "notice_version": NOTICE_VERSION})
    session.commit()
    return profile, code


def find_profile(session: Session, code: str) -> CandidateProfile | None:
    if not code.strip():
        return None
    query = select(CandidateProfile).where(CandidateProfile.access_code_hash == hash_access_code(code))
    return session.exec(query).first()


def get_profile(session: Session, profile_id: int | None) -> CandidateProfile | None:
    return session.get(CandidateProfile, profile_id) if profile_id else None


def update_profile(
    session: Session, profile: CandidateProfile, *, full_name: str, email: str, phone: str, location: str, headline: str
) -> None:
    if not full_name.strip():
        raise CandidateError("Enter your name as it should appear on your resume.")
    profile.full_name = full_name.strip()[:MAX_FIELD_CHARS]
    profile.email = email.strip()[:MAX_FIELD_CHARS]
    profile.phone = phone.strip()[:MAX_FIELD_CHARS]
    profile.location = _clean_location(location)[:MAX_FIELD_CHARS]
    profile.headline = headline.strip()[:MAX_FIELD_CHARS]
    profile.updated_at = utcnow()
    session.commit()


def delete_profile(session: Session, profile: CandidateProfile) -> None:
    """Delete the profile and everything attached to it. The audit log keeps only the id and counts."""
    profile_id = profile.id
    facts = list(session.exec(select(CoreFact).where(CoreFact.profile_id == profile_id)))
    versions = list(session.exec(select(ResumeVersion).where(ResumeVersion.profile_id == profile_id)))
    for row in [*facts, *versions]:
        session.delete(row)
    session.flush()
    session.delete(profile)
    log_event(
        session,
        "candidate_data_deleted",
        payload={"profile_id": profile_id, "facts_deleted": len(facts), "versions_deleted": len(versions)},
    )
    session.commit()


# --- Core facts --------------------------------------------------------------------------------------


def list_facts(session: Session, profile_id: int) -> list[CoreFact]:
    facts = session.exec(select(CoreFact).where(CoreFact.profile_id == profile_id)).all()
    return sorted(facts, key=lambda f: (FACT_CATEGORIES.index(f.category), f.order, f.id))


def fact_views(facts: Sequence[CoreFact]) -> list[FactView]:
    return [
        FactView(id=f.id, category=f.category, title=f.title, organization=f.organization, start=f.start, end=f.end, details=f.details)
        for f in facts
    ]


def _clean_fact(category: str, title: str, organization: str, start: str, end: str, details: str) -> dict:
    if category not in FACT_CATEGORIES:
        raise CandidateError(f"Choose one of: {', '.join(FACT_CATEGORIES)}.")
    if not title.strip():
        labels = {"skills": "the skill", "education": "the degree or program", "certifications": "the certification", "experience": "the job title"}
        raise CandidateError(f"Enter {labels[category]}.")
    if len(details) > MAX_DETAILS_CHARS:
        raise CandidateError(f"Keep the details under {MAX_DETAILS_CHARS:,} characters.")
    return {
        "category": category,
        "title": " ".join(title.split())[:MAX_FIELD_CHARS],
        "organization": " ".join(organization.split())[:MAX_FIELD_CHARS],
        "start": start.strip()[:40],
        "end": end.strip()[:40],
        "details": "\n".join(line.strip() for line in details.splitlines() if line.strip()),
    }


def _log_change(session: Session, profile_id: int, action: str, category: str) -> None:
    count = len(session.exec(select(CoreFact).where(CoreFact.profile_id == profile_id)).all())
    log_event(session, "candidate_facts_changed", payload={"profile_id": profile_id, "action": action, "category": category, "fact_count": count})


def add_fact(
    session: Session,
    profile: CandidateProfile,
    *,
    category: str,
    title: str,
    organization: str = "",
    start: str = "",
    end: str = "",
    details: str = "",
) -> CoreFact:
    existing = list_facts(session, profile.id)
    if len(existing) >= MAX_FACTS:
        raise CandidateError(f"A profile holds up to {MAX_FACTS} facts. Remove one before adding another.")
    fields = _clean_fact(category, title, organization, start, end, details)
    order = max((f.order for f in existing if f.category == category), default=-1) + 1
    fact = CoreFact(profile_id=profile.id, order=order, **fields)
    session.add(fact)
    session.flush()
    _log_change(session, profile.id, "added", category)
    session.commit()
    return fact


def update_fact(session: Session, fact: CoreFact, *, title: str, organization: str = "", start: str = "", end: str = "", details: str = "") -> None:
    fields = _clean_fact(fact.category, title, organization, start, end, details)
    for name, value in fields.items():
        setattr(fact, name, value)
    fact.updated_at = utcnow()
    session.flush()
    _log_change(session, fact.profile_id, "edited", fact.category)
    session.commit()


def delete_fact(session: Session, fact: CoreFact) -> None:
    profile_id, category = fact.profile_id, fact.category
    session.delete(fact)
    session.flush()
    _log_change(session, profile_id, "removed", category)
    session.commit()
