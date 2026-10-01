"""Append-only audit log: write events, query them, export them to CSV.

There is intentionally no function here (or anywhere else) that updates or deletes an event.
Payloads must not contain raw PII: log display labels, hashes, and masked text only.
"""

from __future__ import annotations

import csv
import hashlib
import io
from typing import Any, Iterable, Sequence

from sqlmodel import Session, select

from talentsift.models import AuditEvent, dumps, loads, utcnow

# Event types from the brief, plus a few extensions (ext) documented in docs/decisions.md.
EVENT_TYPES = (
    "rubric_drafted",
    "rubric_approved",
    "role_saved",  # (ext) role created, edited, duplicated, or versioned by the manager
    "resume_ingested",
    "resume_masked",
    "run_started",  # (ext)
    "llm_request",
    "llm_response",
    "llm_fallback",  # (ext) model rejected structured outputs; plain JSON instructions used
    "validation_failed",
    "evidence_unverified",
    "score_computed",
    "ranking_computed",
    "auto_reject",
    "override",
    "run_finished",  # (ext)
    "fairness_check",  # (ext)
    "export",
)

CSV_COLUMNS = (
    "id",
    "timestamp",
    "event_type",
    "run_id",
    "role_id",
    "applicant_id",
    "model",
    "provider",
    "prompt_version",
    "input_hash",
    "payload_json",
)


def sha256_text(text: str) -> str:
    """Hex SHA-256 of a string (used for `input_hash` and cache keys)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def log_event(
    session: Session,
    event_type: str,
    *,
    payload: dict[str, Any] | None = None,
    run_id: int | None = None,
    role_id: int | None = None,
    applicant_id: int | None = None,
    model: str | None = None,
    provider: str | None = None,
    prompt_version: str | None = None,
    input_hash: str | None = None,
) -> AuditEvent:
    """Append one event. The caller owns the transaction (commit happens with the related work)."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown audit event type: {event_type!r}")
    event = AuditEvent(
        timestamp=utcnow(),
        event_type=event_type,
        run_id=run_id,
        role_id=role_id,
        applicant_id=applicant_id,
        model=model,
        provider=provider,
        prompt_version=prompt_version,
        input_hash=input_hash,
        payload_json=dumps(payload or {}),
    )
    session.add(event)
    session.flush()  # assigns the id so callers can reference it
    return event


def list_events(
    session: Session,
    *,
    run_id: int | None = None,
    role_id: int | None = None,
    applicant_id: int | None = None,
    event_types: Sequence[str] | None = None,
    limit: int | None = None,
    newest_first: bool = False,
) -> list[AuditEvent]:
    """Read events with optional filters."""
    query = select(AuditEvent)
    if run_id is not None:
        query = query.where(AuditEvent.run_id == run_id)
    if role_id is not None:
        query = query.where(AuditEvent.role_id == role_id)
    if applicant_id is not None:
        query = query.where(AuditEvent.applicant_id == applicant_id)
    if event_types:
        query = query.where(AuditEvent.event_type.in_(list(event_types)))
    query = query.order_by(AuditEvent.id.desc() if newest_first else AuditEvent.id)
    if limit:
        query = query.limit(limit)
    return list(session.exec(query))


def event_payload(event: AuditEvent) -> dict[str, Any]:
    return loads(event.payload_json, default={})


def event_to_row(event: AuditEvent) -> dict[str, Any]:
    return {column: getattr(event, column) for column in CSV_COLUMNS}


def events_to_csv(events: Iterable[AuditEvent]) -> str:
    """Render events as CSV text (one row per event, payload kept as JSON)."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for event in events:
        writer.writerow(event_to_row(event))
    return buffer.getvalue()


def record_export(session: Session, *, row_count: int, filters: dict[str, Any], csv_text: str) -> AuditEvent:
    """Log that the audit log (or a filtered view of it) was exported."""
    event = log_event(
        session,
        "export",
        payload={"rows": row_count, "filters": filters, "format": "csv"},
        input_hash=sha256_text(csv_text),
    )
    session.commit()
    return event
