"""The fact-anchored drafting assistant, and the candidate's saved resume versions.

Flow (docs/drafting_guard.md):

1. The model receives the candidate's core facts and the pasted job description, never their name or
   contact details, and returns a `ResumeDraft` (summary, skills, bullets per experience fact).
2. The no-new-facts guard checks the draft in code. On a failure the model gets one retry with the list of
   problems. If the retry fails too (or the AI call fails), the assistant falls back to the fact template,
   which arranges the candidate's own words and always passes the guard.
3. The candidate reads the draft and approves it. Only then is it saved as a named version, and the guard
   runs once more at save time against the facts as they are then.

The model's prompts and replies are not written to the audit log, because that log is visible on the
employer side and they contain the candidate's facts. The log records what happened (source, guard result,
model, hashes), not the content.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import Sequence

from sqlmodel import Session, select

from talentsift.audit import log_event, sha256_text
from talentsift.candidates import CandidateError, fact_views, list_facts
from talentsift.config import Settings
from talentsift.draft_template import FactView, template_draft
from talentsift.guard import GuardReport, check_draft
from talentsift.llm.base import LLMClient, call_with_validation
from talentsift.models import CATEGORY_LABELS, CandidateProfile, ResumeVersion, dumps, loads
from talentsift.prompts import DRAFT_PROMPT, load_prompt
from talentsift.schemas import ResumeDraft

MAX_ROLE_CHARS = 12_000
MAX_VERSIONS = 30


@dataclass
class DraftOutcome:
    draft: ResumeDraft
    report: GuardReport  # always passed: a failing draft is never returned
    source: str  # "ai" (the model's wording passed the guard) or "template" (the candidate's own wording)
    message: str  # what happened, in plain language, for the candidate
    body: str  # the resume as Markdown
    role_title: str = ""
    role_text: str = ""
    model_used: str = ""
    provider_used: str = ""
    prompt_version: str = ""
    blocked: list[dict] = field(default_factory=list)  # what the guard rejected along the way


# --- Rendering ---------------------------------------------------------------------------------------


def _dates(fact: FactView) -> str:
    return " – ".join(part for part in (fact.start, fact.end) if part)


def _heading(fact: FactView) -> str:
    parts = [f"**{fact.title}**", fact.organization, _dates(fact)]
    return " · ".join(part for part in parts if part)


def render_markdown(profile: CandidateProfile, facts: Sequence[FactView], draft: ResumeDraft) -> str:
    """The resume as Markdown. Names, titles, organizations, and dates come from the profile and facts."""
    by_id = {f.id: f for f in facts}
    lines = [f"# {profile.full_name}"]
    contact = " · ".join(part for part in (profile.location, profile.email, profile.phone) if part)
    if contact:
        lines.append(contact)
    if profile.headline:
        lines += ["", f"*{profile.headline}*"]
    if draft.summary.strip():
        lines += ["", "## Summary", draft.summary.strip()]
    if draft.skills:
        lines += ["", "## Skills", " · ".join(draft.skills)]
    if draft.experience:
        lines += ["", "## Experience"]
        for entry in draft.experience:
            lines += ["", _heading(by_id[entry.fact_id])]
            lines += [f"- {bullet.strip()}" for bullet in entry.bullets if bullet.strip()]
    for title, ids in (("Education", draft.education_ids), ("Certifications", draft.certification_ids)):
        if ids:
            lines += ["", f"## {title}"]
            for fact_id in ids:
                fact = by_id[fact_id]
                lines += ["", _heading(fact)]
                lines += [f"- {detail}" for detail in fact.detail_lines]
    return "\n".join(lines).strip() + "\n"


# --- Drafting ----------------------------------------------------------------------------------------


def generate_draft(
    session: Session,
    profile: CandidateProfile,
    client: LLMClient,
    settings: Settings,
    *,
    role_title: str = "",
    role_text: str = "",
) -> DraftOutcome:
    """Draft a resume for a target role from the candidate's facts, guarded against new facts."""
    if profile.consent_at is None:
        raise CandidateError("Accept the AI notice on your profile before drafting.")
    facts = fact_views(list_facts(session, profile.id))
    if not facts:
        raise CandidateError("Add at least one core fact to your profile before drafting.")
    role_title, role_text = role_title.strip(), role_text.strip()[:MAX_ROLE_CHARS]

    prompt = load_prompt(DRAFT_PROMPT, settings.prompts_dir)
    user = prompt.render_user(
        headline=profile.headline,
        facts_json=json.dumps([f.as_prompt_dict() for f in facts], indent=1, ensure_ascii=False),
        role_title=role_title,
        role_text=role_text or "(no job description given: write a general-purpose resume)",
    )
    blocked: list[dict] = []
    reports: list[GuardReport] = []

    def guard(candidate_draft: ResumeDraft) -> None:
        report = check_draft(candidate_draft, facts, role_text, profile.headline)
        reports.append(report)
        if not report.passed:
            blocked.extend(v.as_dict() for v in report.violations)
            raise ValueError(report.retry_message())

    call = call_with_validation(client, prompt.system, user, ResumeDraft, check=guard)
    result = call.last_result
    model_used = result.model_used if result else client.model
    provider_used = result.provider_used if result else client.provider

    if call.ok:
        draft, report, source = call.value, reports[-1], "ai"
        message = "Draft ready. Every line was checked against your core facts."
        if blocked:
            message = "The first draft added details that are not in your facts, so it was rejected and redone. " + message
    else:
        draft = template_draft(facts, role_text, profile.headline)
        report, source = check_draft(draft, facts, role_text, profile.headline), "template"
        if blocked:
            message = (
                "The AI's wording added details that are not in your core facts, twice, so it was not used. "
                "This draft arranges your own wording for the role instead."
            )
        else:
            reason = call.fatal_error or call.last_error or "no reply"
            message = f"The AI was not available ({reason}). This draft arranges your own wording for the role instead."
        if not report.passed:  # cannot happen by construction; refuse rather than show an unchecked draft
            raise CandidateError("The draft could not be verified against your facts. Please try again.")

    if blocked:
        log_event(
            session,
            "draft_guard_blocked",
            model=model_used,
            provider=provider_used,
            prompt_version=prompt.version,
            payload={
                "profile_id": profile.id,
                "attempts": len(call.attempts),
                "violation_kinds": sorted({v["kind"] for v in blocked}),
                "violation_count": len(blocked),
                "fell_back_to_template": source == "template",
            },
        )
    log_event(
        session,
        "resume_drafted",
        model=model_used,
        provider=provider_used,
        prompt_version=prompt.version,
        input_hash=report.facts_hash,
        payload={
            "profile_id": profile.id,
            "source": source,
            "guard_passed": report.passed,
            "lines_checked": report.lines_checked,
            "notes": len(report.notes),
            "fact_count": len(facts),
            "role_text_sha256": sha256_text(role_text),
            "prompt_sha256": prompt.sha256,
            "tokens": call.total_tokens,
            "cost": call.total_cost,
        },
    )
    session.commit()
    return DraftOutcome(
        draft=draft,
        report=report,
        source=source,
        message=message,
        body=render_markdown(profile, facts, draft),
        role_title=role_title,
        role_text=role_text,
        model_used=model_used if source == "ai" else "",
        provider_used=provider_used if source == "ai" else "",
        prompt_version=prompt.version,
        blocked=blocked,
    )


# --- Saved versions ----------------------------------------------------------------------------------


def list_versions(session: Session, profile_id: int) -> list[ResumeVersion]:
    query = select(ResumeVersion).where(ResumeVersion.profile_id == profile_id).order_by(ResumeVersion.id)
    return list(session.exec(query))


def save_version(session: Session, profile: CandidateProfile, outcome: DraftOutcome, *, label: str, approved: bool) -> ResumeVersion:
    """Save a draft the candidate approved. The guard runs again here against the current facts."""
    if not approved:
        raise CandidateError("Confirm that you have read the draft and that it is accurate before saving.")
    label = " ".join(label.split())
    if not label:
        raise CandidateError("Give this version a name, for example 'Analyst roles'.")
    existing = list_versions(session, profile.id)
    if any(v.label.casefold() == label.casefold() for v in existing):
        raise CandidateError(f"You already have a version named '{label}'. Choose another name.")
    if len(existing) >= MAX_VERSIONS:
        raise CandidateError(f"A profile holds up to {MAX_VERSIONS} versions. Delete one before saving another.")

    facts = fact_views(list_facts(session, profile.id))
    report = check_draft(outcome.draft, facts, outcome.role_text, profile.headline)
    if not report.passed:
        raise CandidateError("Your core facts changed since this draft was written, and it no longer matches them. Draft again.")

    version = ResumeVersion(
        profile_id=profile.id,
        label=label[:120],
        target_role_title=outcome.role_title[:200],
        target_role_text=outcome.role_text,
        body=render_markdown(profile, facts, outcome.draft),
        draft_json=outcome.draft.model_dump_json(),
        source=outcome.source,
        guard_json=dumps(report.as_dict()),
        facts_hash=report.facts_hash,
        model_used=outcome.model_used,
        provider_used=outcome.provider_used,
        prompt_version=outcome.prompt_version,
    )
    session.add(version)
    session.flush()
    log_event(
        session,
        "resume_version_saved",
        model=outcome.model_used or None,
        provider=outcome.provider_used or None,
        prompt_version=outcome.prompt_version or None,
        input_hash=report.facts_hash,
        payload={"profile_id": profile.id, "version_id": version.id, "source": outcome.source, "guard_passed": True, "body_sha256": sha256_text(version.body)},
    )
    session.commit()
    return version


def delete_version(session: Session, version: ResumeVersion) -> None:
    session.delete(version)
    session.commit()


def version_stats(version: ResumeVersion) -> dict:
    """One row of the compare list."""
    draft = loads(version.draft_json, default={}) or {}
    return {
        "id": version.id,
        "Version": version.label,
        "Target role": version.target_role_title or "(general)",
        "Wording": "AI, checked against your facts" if version.source == "ai" else "Your own wording",
        "Skills": len(draft.get("skills", [])),
        "Bullets": sum(len(entry.get("bullets", [])) for entry in draft.get("experience", [])),
        "Words": len(version.body.split()),
        "Saved": version.created_at,
    }


@dataclass
class Comparison:
    left: ResumeVersion
    right: ResumeVersion
    diff: list[str]  # unified diff lines of the two bodies
    lines_only_left: int
    lines_only_right: int
    skills_only_left: list[str]
    skills_only_right: list[str]
    skills_reordered: bool
    same_facts: bool  # both versions were checked against the same set of facts

    @property
    def identical(self) -> bool:
        return not self.diff


def compare_versions(left: ResumeVersion, right: ResumeVersion) -> Comparison:
    """What differs between two saved versions: wording, skills, and whether the facts changed between them."""
    a, b = left.body.splitlines(), right.body.splitlines()
    diff = list(difflib.unified_diff(a, b, fromfile=left.label, tofile=right.label, lineterm="", n=1))
    changes = [line for line in diff if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    left_skills = (loads(left.draft_json, default={}) or {}).get("skills", [])
    right_skills = (loads(right.draft_json, default={}) or {}).get("skills", [])
    return Comparison(
        left=left,
        right=right,
        diff=diff,
        lines_only_left=sum(line.startswith("-") for line in changes),
        lines_only_right=sum(line.startswith("+") for line in changes),
        skills_only_left=[s for s in left_skills if s not in right_skills],
        skills_only_right=[s for s in right_skills if s not in left_skills],
        skills_reordered=left_skills != right_skills and sorted(left_skills) == sorted(right_skills),
        same_facts=left.facts_hash == right.facts_hash,
    )


def facts_by_category(facts: Sequence[FactView]) -> dict[str, list[FactView]]:
    return {CATEGORY_LABELS[c]: [f for f in facts if f.category == c] for c in CATEGORY_LABELS}
