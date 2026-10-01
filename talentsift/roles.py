"""Roles and their versioned rubrics: create, edit, duplicate, approve.

Versioning rules (docs/decisions.md D-011):
* Criterion rows belong to one rubric version (`Criterion.role_version`).
* A draft version is edited in place. Saving changes to an approved version creates version N+1 as a draft.
* Screening requires the current version to be approved, so approved rubrics never change under a run.
* Threshold and top N are policy settings: changing them never creates a version (each run snapshots them).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlmodel import Session, select

from talentsift.audit import event_payload, list_events, log_event, sha256_text
from talentsift.models import (
    CRITERION_SOURCES,
    CRITERION_TYPES,
    CRITERION_WEIGHTS,
    Criterion,
    Role,
    dumps,
    utcnow,
)

MAX_CRITERIA = 15


class RoleError(ValueError):
    """A role or rubric change that cannot be saved; the message is shown to the manager."""


@dataclass
class CriterionInput:
    """One rubric row as edited in the UI (or produced by the AI draft)."""

    name: str
    description: str = ""
    type: str = "nice_to_have"
    weight: str = "medium"
    source: str = "manager"
    proxy_warning: str = ""
    id: int | None = None  # set when the row came from an existing criterion

    def content(self) -> tuple[str, str, str, str]:
        return (self.name.strip(), self.description.strip(), self.type, self.weight)

    @classmethod
    def from_criterion(cls, criterion: Criterion) -> "CriterionInput":
        return cls(
            name=criterion.name,
            description=criterion.description,
            type=criterion.type,
            weight=criterion.weight,
            source=criterion.source,
            proxy_warning=criterion.proxy_warning,
            id=criterion.id,
        )


@dataclass
class SaveOutcome:
    action: str  # "new_version", "draft_updated", "policy_updated", or "no_change"
    version: int
    message: str


# --- Reading ---------------------------------------------------------------------------------------


def list_roles(session: Session) -> list[Role]:
    return list(session.exec(select(Role).order_by(Role.id)))


def get_role(session: Session, role_id: int) -> Role:
    role = session.get(Role, role_id)
    if role is None:
        raise RoleError(f"Role {role_id} does not exist")
    return role


def criteria_for_version(session: Session, role_id: int, version: int) -> list[Criterion]:
    query = (
        select(Criterion)
        .where(Criterion.role_id == role_id, Criterion.role_version == version)
        .order_by(Criterion.order, Criterion.id)
    )
    return list(session.exec(query))


def current_criteria(session: Session, role: Role) -> list[Criterion]:
    return criteria_for_version(session, role.id, role.version)


def rubric_snapshot(role: Role, criteria: Iterable[Criterion]) -> dict:
    """Everything that defines a rubric version. Logged on approval and with every run."""
    return {
        "role_id": role.id,
        "version": role.version,
        "title": role.title,
        "summary": role.summary,
        "criteria": [
            {
                "criterion_id": c.id,
                "name": c.name,
                "description": c.description,
                "type": c.type,
                "weight": c.weight,
                "order": c.order,
                "source": c.source,
            }
            for c in criteria
        ],
    }


def rubric_hash(role: Role, criteria: Iterable[Criterion]) -> str:
    return sha256_text(dumps(rubric_snapshot(role, criteria)))


# --- Validation ------------------------------------------------------------------------------------


def validate_criteria(criteria: list[CriterionInput]) -> None:
    if not criteria:
        raise RoleError("Add at least one criterion.")
    if len(criteria) > MAX_CRITERIA:
        raise RoleError(f"Keep the rubric to {MAX_CRITERIA} criteria or fewer.")
    names = [c.name.strip().lower() for c in criteria]
    if any(not name for name in names):
        raise RoleError("Every criterion needs a name.")
    if len(set(names)) != len(names):
        raise RoleError("Criterion names must be unique.")
    for c in criteria:
        if c.type not in CRITERION_TYPES:
            raise RoleError(f"'{c.name}': type must be one of {CRITERION_TYPES}.")
        if c.weight not in CRITERION_WEIGHTS:
            raise RoleError(f"'{c.name}': weight must be one of {CRITERION_WEIGHTS}.")
        if c.source not in CRITERION_SOURCES:
            raise RoleError(f"'{c.name}': unknown source {c.source!r}.")


def _validate_policy(threshold: int, top_n: int) -> None:
    if not 0 <= threshold <= 100:
        raise RoleError("The auto-reject threshold must be between 0 and 100.")
    if not 1 <= top_n <= 200:
        raise RoleError("Top N must be between 1 and 200.")


# --- Writing ---------------------------------------------------------------------------------------


def _add_criteria(
    session: Session, role: Role, version: int, criteria: list[CriterionInput], start_order: int = 0
) -> None:
    for order, c in enumerate(criteria, start=start_order):
        session.add(
            Criterion(
                role_id=role.id,
                role_version=version,
                name=c.name.strip(),
                description=c.description.strip(),
                type=c.type,
                weight=c.weight,
                order=order,
                source=c.source,
                proxy_warning=c.proxy_warning,
            )
        )


def create_role(
    session: Session,
    *,
    title: str,
    criteria: list[CriterionInput],
    posting_text: str = "",
    summary: str = "",
    auto_reject_threshold: int = 40,
    top_n: int = 5,
    log_action: str | None = "created",
    commit: bool = True,
) -> Role:
    """Create a role at version 1 (a draft until approved)."""
    if not title.strip():
        raise RoleError("Give the role a title.")
    validate_criteria(criteria)
    _validate_policy(auto_reject_threshold, top_n)
    role = Role(
        title=title.strip(),
        summary=summary.strip(),
        posting_text=posting_text,
        auto_reject_threshold=auto_reject_threshold,
        top_n=top_n,
    )
    session.add(role)
    session.flush()
    _add_criteria(session, role, 1, criteria)
    session.flush()
    if log_action:
        log_event(
            session,
            "role_saved",
            role_id=role.id,
            payload={"action": log_action, "version": 1, "criteria_count": len(criteria), "title": role.title},
        )
    if commit:
        session.commit()
    return role


def save_role(
    session: Session,
    role: Role,
    *,
    title: str,
    summary: str,
    posting_text: str,
    criteria: list[CriterionInput],
    auto_reject_threshold: int,
    top_n: int,
    changed_rows_source: str = "manager",
) -> SaveOutcome:
    """Save edits from the Roles page, creating a new version when an approved rubric changes.

    Rows that are new or edited get `changed_rows_source` ("manager" for UI edits, "ai_draft" for a redraft).
    """
    if not title.strip():
        raise RoleError("Give the role a title.")
    validate_criteria(criteria)
    _validate_policy(auto_reject_threshold, top_n)

    existing = current_criteria(session, role)
    old_by_id = {c.id: c for c in existing}
    rubric_changed = (
        title.strip() != role.title
        or summary.strip() != role.summary
        or [c.content() for c in criteria] != [CriterionInput.from_criterion(c).content() for c in existing]
    )
    policy_changed = (auto_reject_threshold, top_n) != (role.auto_reject_threshold, role.top_n)
    posting_changed = posting_text != role.posting_text

    # Rows that were edited or added take the new source; untouched rows keep theirs.
    for c in criteria:
        old = old_by_id.get(c.id)
        if old is None or CriterionInput.from_criterion(old).content() != c.content():
            c.source = changed_rows_source
            if old is not None and old.proxy_warning and c.content()[:2] != (old.name, old.description):
                c.proxy_warning = ""  # the flagged wording was changed; the old warning no longer applies

    action, version = "no_change", role.version
    if rubric_changed and role.is_approved:
        version = role.version + 1
        _add_criteria(session, role, version, criteria)
        role.version = version
        action = "new_version"
    elif rubric_changed:
        kept_ids = {c.id for c in criteria if c.id in old_by_id}
        for old in existing:
            if old.id not in kept_ids:
                session.delete(old)  # safe: a draft version has never been used for screening
        for order, c in enumerate(criteria):
            if c.id in old_by_id:
                row = old_by_id[c.id]
                row.name, row.description, row.type, row.weight = c.content()
                row.order, row.source, row.proxy_warning = order, c.source, c.proxy_warning
            else:
                _add_criteria(session, role, version, [c], start_order=order)
        action = "draft_updated"
    elif policy_changed or posting_changed:
        action = "policy_updated"

    if action == "no_change":
        return SaveOutcome(action, version, "Nothing changed.")

    role.title, role.summary, role.posting_text = title.strip(), summary.strip(), posting_text
    role.auto_reject_threshold, role.top_n = auto_reject_threshold, top_n
    role.updated_at = utcnow()
    session.flush()
    log_event(
        session,
        "role_saved",
        role_id=role.id,
        payload={
            "action": action,
            "version": version,
            "rubric_changed": rubric_changed,
            "policy_changed": policy_changed,
            "posting_changed": posting_changed,
            "auto_reject_threshold": auto_reject_threshold,
            "top_n": top_n,
            "criteria_count": len(criteria),
        },
    )
    session.commit()
    messages = {
        "new_version": f"Saved as version {version} (draft). Approve it before screening.",
        "draft_updated": f"Draft version {version} updated. Approve it before screening.",
        "policy_updated": "Settings saved. The approved rubric is unchanged.",
    }
    return SaveOutcome(action, version, messages[action])


def approve_role(session: Session, role: Role) -> None:
    """Approve the current rubric version so it can be used for screening."""
    if role.is_approved:
        raise RoleError(f"Version {role.version} is already approved.")
    criteria = current_criteria(session, role)
    if not criteria:
        raise RoleError("Add at least one criterion before approving.")
    role.approved_version = role.version
    role.approved_at = utcnow()
    role.updated_at = role.approved_at
    log_event(
        session,
        "rubric_approved",
        role_id=role.id,
        payload={
            "version": role.version,
            "rubric_hash": rubric_hash(role, criteria),
            "snapshot": rubric_snapshot(role, criteria),
            "auto_reject_threshold": role.auto_reject_threshold,
            "top_n": role.top_n,
            "proxy_warnings_open": [c.name for c in criteria if c.proxy_warning],
        },
    )
    session.commit()


def discard_draft(session: Session, role: Role) -> None:
    """Throw away an unapproved draft version and return to the last approved version."""
    if role.approved_version is None or role.is_approved:
        raise RoleError("There is no draft on top of an approved version to discard.")
    draft_version = role.version
    for criterion in current_criteria(session, role):
        session.delete(criterion)  # safe: a draft version has never been used for screening
    role.version = role.approved_version
    # Title and summary live on the role row; restore them from the approval snapshot in the audit log.
    for event in list_events(session, role_id=role.id, event_types=["rubric_approved"], newest_first=True):
        snapshot = event_payload(event).get("snapshot", {})
        if snapshot.get("version") == role.approved_version:
            role.title, role.summary = snapshot["title"], snapshot["summary"]
            break
    role.updated_at = utcnow()
    session.flush()
    log_event(
        session,
        "role_saved",
        role_id=role.id,
        payload={"action": "draft_discarded", "discarded_version": draft_version, "version": role.version},
    )
    session.commit()


def duplicate_role(session: Session, role: Role) -> Role:
    """Copy a role's current rubric and settings into a new role (version 1, draft)."""
    criteria = [CriterionInput.from_criterion(c) for c in current_criteria(session, role)]
    for c in criteria:
        c.id = None
    copy = create_role(
        session,
        title=f"{role.title} (copy)",
        criteria=criteria,
        posting_text=role.posting_text,
        summary=role.summary,
        auto_reject_threshold=role.auto_reject_threshold,
        top_n=role.top_n,
        log_action=None,
        commit=False,
    )
    log_event(
        session,
        "role_saved",
        role_id=copy.id,
        payload={"action": "duplicated", "from_role_id": role.id, "from_version": role.version, "version": 1},
    )
    session.commit()
    return copy


def version_history(session: Session, role: Role) -> list[dict]:
    """One row per rubric version: number, criteria count, approval state."""
    versions = sorted({c.role_version for c in session.exec(select(Criterion).where(Criterion.role_id == role.id))})
    return [
        {
            "version": version,
            "criteria": len(criteria_for_version(session, role.id, version)),
            "status": "approved"
            if version == role.approved_version
            else ("draft" if version == role.version else "superseded"),
        }
        for version in versions
    ]
