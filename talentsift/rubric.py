"""Draft a rubric from a pasted job posting with the LLM. The manager edits and approves it afterwards."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import Session

from talentsift.audit import log_event, log_llm_attempts, sha256_text
from talentsift.config import Settings
from talentsift.llm.base import Attempt, LLMClient, call_with_validation, schema_name
from talentsift.models import Role
from talentsift.prompts import RUBRIC_PROMPT, load_prompt
from talentsift.roles import CriterionInput, RoleError, create_role, save_role
from talentsift.schemas import RubricDraft, check_rubric_draft


@dataclass
class DraftOutcome:
    role: Role | None
    error: str = ""
    proxy_flags: list[str] = field(default_factory=list)  # names of criteria flagged as possible proxies
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.role is not None


def _ask_for_draft(client: LLMClient, settings: Settings, posting_text: str, title: str):
    prompt = load_prompt(RUBRIC_PROMPT, settings.prompts_dir)
    user = prompt.render_user(role_title=title.strip(), posting_text=posting_text.strip())
    call = call_with_validation(client, prompt.system, user, RubricDraft, check=check_rubric_draft)
    return prompt, call


def _criteria_from_draft(draft: RubricDraft) -> list[CriterionInput]:
    return [
        CriterionInput(
            name=c.name.strip(),
            description=c.description.strip(),
            type=c.type,
            weight=c.weight,
            source="ai_draft",
            proxy_warning=c.proxy_note.strip() if c.proxy_risk else "",
        )
        for c in draft.criteria
    ]


def _log_draft(session: Session, *, role_id: int | None, prompt, call, client: LLMClient, posting_text: str, extra: dict) -> None:
    log_llm_attempts(
        session,
        call.attempts,
        schema_name=schema_name(RubricDraft),
        prompt_version=prompt.version,
        model_requested=client.model,
        role_id=role_id,
        context={"purpose": "rubric_draft"},
    )
    if call.ok:
        draft: RubricDraft = call.value
        result = call.last_result
        log_event(
            session,
            "rubric_drafted",
            role_id=role_id,
            model=result.model_used if result else client.model,
            provider=result.provider_used if result else None,
            prompt_version=prompt.version,
            input_hash=sha256_text(posting_text),
            payload={
                **extra,
                "criteria_count": len(draft.criteria),
                "proxy_flags": [c.name for c in draft.criteria if c.proxy_risk],
                "prompt_sha256": prompt.sha256,
            },
        )


def draft_rubric(
    session: Session,
    client: LLMClient,
    settings: Settings,
    *,
    posting_text: str,
    title: str = "",
    auto_reject_threshold: int = 40,
    top_n: int = 5,
) -> DraftOutcome:
    """Create a new role (version 1, draft) whose criteria the AI drafted from the posting."""
    if len(posting_text.strip()) < 40:
        return DraftOutcome(role=None, error="Paste the full job posting (at least a few sentences).")
    prompt, call = _ask_for_draft(client, settings, posting_text, title)
    if not call.ok:
        _log_draft(session, role_id=None, prompt=prompt, call=call, client=client, posting_text=posting_text, extra={})
        session.commit()
        reason = call.fatal_error or call.last_error or "unknown error"
        return DraftOutcome(role=None, error=f"The AI could not draft a rubric: {reason}", attempts=call.attempts)

    draft: RubricDraft = call.value
    role = create_role(
        session,
        title=title.strip() or draft.role_title.strip() or "Untitled role",
        summary=draft.summary,
        posting_text=posting_text,
        criteria=_criteria_from_draft(draft),
        auto_reject_threshold=auto_reject_threshold,
        top_n=top_n,
        log_action=None,
        commit=False,
    )
    _log_draft(session, role_id=role.id, prompt=prompt, call=call, client=client, posting_text=posting_text, extra={"version": 1, "action": "new_role"})
    session.commit()
    return DraftOutcome(
        role=role, proxy_flags=[c.name for c in draft.criteria if c.proxy_risk], attempts=call.attempts
    )


def redraft_rubric(session: Session, role: Role, client: LLMClient, settings: Settings) -> DraftOutcome:
    """Replace the rubric of an existing role with a fresh AI draft of its posting (as a draft version)."""
    if len(role.posting_text.strip()) < 40:
        return DraftOutcome(role=None, error="This role has no job posting to draft from.")
    prompt, call = _ask_for_draft(client, settings, role.posting_text, role.title)
    if not call.ok:
        _log_draft(session, role_id=role.id, prompt=prompt, call=call, client=client, posting_text=role.posting_text, extra={})
        session.commit()
        return DraftOutcome(role=None, error=f"The AI could not draft a rubric: {call.fatal_error or call.last_error}")
    draft: RubricDraft = call.value
    try:
        outcome = save_role(
            session,
            role,
            title=role.title,
            summary=draft.summary,
            posting_text=role.posting_text,
            criteria=_criteria_from_draft(draft),
            auto_reject_threshold=role.auto_reject_threshold,
            top_n=role.top_n,
            changed_rows_source="ai_draft",
        )
    except RoleError as exc:
        return DraftOutcome(role=None, error=str(exc))
    _log_draft(
        session,
        role_id=role.id,
        prompt=prompt,
        call=call,
        client=client,
        posting_text=role.posting_text,
        extra={"version": outcome.version, "action": "redraft"},
    )
    session.commit()
    return DraftOutcome(role=role, proxy_flags=[c.name for c in draft.criteria if c.proxy_risk], attempts=call.attempts)
