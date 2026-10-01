"""Load the fictional demo: two roles drafted from sample postings, 20 sample resumes, optional screening runs.

Used by `scripts/seed_demo.py`, the "Load demo data" button, and `AUTO_SEED_DEMO` (hosted demos lose their
SQLite file on reboot). Safe to run twice: existing roles (by title) and files (by hash) are skipped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlmodel import Session, select

from talentsift.config import PROJECT_ROOT, Settings
from talentsift.ingest import ingest_folder
from talentsift.llm import LLMClient, build_client
from talentsift.models import Applicant, Role
from talentsift.roles import CriterionInput, approve_role, current_criteria, save_role
from talentsift.rubric import draft_rubric
from talentsift.scoring import run_screening

SAMPLE_RESUMES = PROJECT_ROOT / "data" / "resumes" / "samples"
SAMPLE_ROLES = PROJECT_ROOT / "data" / "samples" / "roles"
DEMO_BATCH = "samples"


@dataclass
class SeedSummary:
    roles_created: list[str] = field(default_factory=list)
    roles_skipped: list[str] = field(default_factory=list)
    removed_proxy_criteria: list[str] = field(default_factory=list)
    applicants_added: int = 0
    applicants_skipped: int = 0
    run_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        parts = [
            f"{len(self.roles_created)} role(s) created",
            f"{self.applicants_added} resume(s) added",
        ]
        if self.removed_proxy_criteria:
            parts.append(f"removed proxy-risk criteria: {', '.join(self.removed_proxy_criteria)}")
        if self.run_ids:
            parts.append(f"screening runs {', '.join(map(str, self.run_ids))}")
        if self.errors:
            parts.append("errors: " + "; ".join(self.errors))
        return ". ".join(parts) + "."


def load_role_profiles() -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(SAMPLE_ROLES.glob("*.json"))]


def seed_demo(
    session: Session,
    settings: Settings,
    *,
    client: LLMClient | None = None,
    screen: bool = False,
) -> SeedSummary:
    """Create the sample roles (AI-drafted, proxy criteria removed, approved) and import the sample resumes."""
    client = client or build_client(settings, "fake")
    summary = SeedSummary()
    seeded_roles: list[Role] = []

    for profile in load_role_profiles():
        existing = session.exec(select(Role).where(Role.title == profile["title"])).first()
        if existing is not None:
            summary.roles_skipped.append(profile["title"])
            seeded_roles.append(existing)
            continue
        outcome = draft_rubric(
            session,
            client,
            settings,
            posting_text=profile["posting_text"],
            title=profile["title"],
            auto_reject_threshold=profile.get("auto_reject_threshold", 40),
            top_n=profile.get("top_n", 5),
        )
        if not outcome.ok:
            summary.errors.append(f"{profile['title']}: {outcome.error}")
            continue
        role = outcome.role
        # Do what a careful manager would: remove criteria the AI flagged as proxies, then approve.
        criteria = current_criteria(session, role)
        flagged = [c.name for c in criteria if c.proxy_warning]
        if flagged:
            save_role(
                session,
                role,
                title=role.title,
                summary=role.summary,
                posting_text=role.posting_text,
                criteria=[CriterionInput.from_criterion(c) for c in criteria if not c.proxy_warning],
                auto_reject_threshold=role.auto_reject_threshold,
                top_n=role.top_n,
            )
            summary.removed_proxy_criteria.extend(flagged)
        approve_role(session, role)
        summary.roles_created.append(role.title)
        seeded_roles.append(role)

    results = ingest_folder(session, SAMPLE_RESUMES, settings=settings, batch=DEMO_BATCH)
    summary.applicants_added = sum(r.outcome == "added" for r in results)
    summary.applicants_skipped = len(results) - summary.applicants_added

    if screen:
        applicants = list(session.exec(select(Applicant).where(Applicant.batch == DEMO_BATCH).order_by(Applicant.id)))
        for role in seeded_roles:
            if role.is_approved and applicants:
                run = run_screening(session, role=role, applicants=applicants, client=client, settings=settings, batch_label=DEMO_BATCH)
                summary.run_ids.append(run.id)
    return summary


def seed_if_empty(session: Session, settings: Settings) -> SeedSummary | None:
    """Seed (and screen offline) only when the database has no roles yet."""
    if session.exec(select(Role)).first() is not None:
        return None
    return seed_demo(session, settings, screen=True)
