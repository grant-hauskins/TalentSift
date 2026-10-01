"""The audit trail is append-only, overrides need reasons, and exports are logged."""

import csv
import inspect
import io
import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from sqlmodel import select

from talentsift import audit
from talentsift.audit import events_to_csv, list_events, record_export
from talentsift.db import AppendOnlyError
from talentsift.llm.fake_client import FakeLLMClient
from talentsift.models import (
    STATUS_AUTO_REJECTED,
    STATUS_NOT_SHORTLISTED,
    STATUS_SHORTLISTED,
    Evaluation,
)
from talentsift.overrides import OverrideError, override_status, overrides_for_run, reinstate
from talentsift.scoring import run_screening

from tests.factories import MEDIUM, STRONG, WEAK, make_applicant, make_role

ROOT = Path(__file__).resolve().parent.parent
SOURCE_FILES = [*ROOT.glob("app.py"), *ROOT.glob("pages/*.py"), *ROOT.glob("talentsift/**/*.py"), *ROOT.glob("scripts/*.py")]

# Any of these would be an update or delete path for the append-only tables.
FORBIDDEN = [
    re.compile(r"\b(update|delete)\(\s*(AuditEvent|Override)\b"),  # SQLAlchemy core statements
    re.compile(r"\b(UPDATE|DELETE\s+FROM)\s+[\"'`]?(audit_event|override)\b", re.IGNORECASE),  # raw SQL
    re.compile(r"\.query\(\s*(AuditEvent|Override)\s*\)[^\n]*\.(update|delete)\("),  # legacy ORM bulk ops
    re.compile(r"session\.delete\(\s*(event|audit_event|override|record)\b"),  # deleting a loaded event
]


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_update_or_delete_code_paths_for_audit_tables(path):
    source = path.read_text(encoding="utf-8")
    for pattern in FORBIDDEN:
        assert not pattern.search(source), f"{path.name} contains an update/delete path: {pattern.pattern}"


def test_audit_module_exposes_no_mutation_functions():
    names = [name for name, obj in inspect.getmembers(audit, inspect.isfunction) if obj.__module__ == audit.__name__]
    assert names, "expected audit functions"
    assert not [n for n in names if re.search(r"update|delete|remove|edit|purge|clear|truncate", n)]


@pytest.fixture
def run(session, settings):
    role = make_role(session, threshold=40, top_n=1)
    applicants = [make_applicant(session, text) for text in (STRONG, MEDIUM, WEAK)]
    return run_screening(session, role=role, applicants=applicants, client=FakeLLMClient(), settings=settings)


def by_status(session, run, status):
    return session.exec(select(Evaluation).where(Evaluation.run_id == run.id, Evaluation.status == status)).one()


@pytest.mark.parametrize("reason", ["", "   ", "ok", "too short"])
def test_override_without_a_real_reason_is_rejected(session, run, reason):
    evaluation = by_status(session, run, STATUS_NOT_SHORTLISTED)
    with pytest.raises(OverrideError, match="reason"):
        override_status(session, evaluation, STATUS_SHORTLISTED, reason)
    assert evaluation.status == STATUS_NOT_SHORTLISTED
    assert list_events(session, event_types=["override"]) == []


def test_reinstate_without_reason_is_rejected(session, run):
    rejected = by_status(session, run, STATUS_AUTO_REJECTED)
    with pytest.raises(OverrideError):
        reinstate(session, rejected, "")
    assert rejected.status == STATUS_AUTO_REJECTED


def test_reinstatement_is_logged_and_reversible(session, run):
    rejected = by_status(session, run, STATUS_AUTO_REJECTED)
    reinstate(session, rejected, "Has relevant retail analytics experience we value")
    assert rejected.status == STATUS_NOT_SHORTLISTED
    assert rejected.auto_status == STATUS_AUTO_REJECTED  # the automated decision stays on record

    [record] = overrides_for_run(session, run)
    assert (record.from_status, record.to_status) == (STATUS_AUTO_REJECTED, STATUS_NOT_SHORTLISTED)
    [event] = list_events(session, event_types=["override"])
    assert event.applicant_id == rejected.applicant_id and event.run_id == run.id
    assert '"kind": "reinstate"' in event.payload_json
    assert "retail analytics" in event.payload_json

    record.reason = "rewritten history"
    with pytest.raises(AppendOnlyError):
        session.flush()
    session.rollback()
    with session.connection() as connection:  # the database refuses too, whatever code tries
        with pytest.raises(DatabaseError, match="append-only"):
            connection.execute(text("UPDATE override SET reason = 'rewritten history'"))
        with pytest.raises(DatabaseError, match="append-only"):
            connection.execute(text("DELETE FROM override"))


def test_override_to_same_status_or_auto_rejected_is_refused(session, run):
    shortlisted = by_status(session, run, STATUS_SHORTLISTED)
    with pytest.raises(OverrideError):
        override_status(session, shortlisted, STATUS_SHORTLISTED, "No change requested here")
    with pytest.raises(OverrideError):
        override_status(session, shortlisted, STATUS_AUTO_REJECTED, "People cannot auto-reject")


def test_only_rejected_applicants_can_be_reinstated(session, run):
    with pytest.raises(OverrideError):
        reinstate(session, by_status(session, run, STATUS_SHORTLISTED), "Not rejected in the first place")


def test_csv_export_contains_events_and_is_logged(session, run):
    events = list_events(session, run_id=run.id)
    text = events_to_csv(events)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == len(events)
    assert rows[0]["event_type"] == "run_started"
    assert set(rows[0]) >= {"timestamp", "event_type", "run_id", "applicant_id", "model", "provider", "prompt_version", "input_hash", "payload_json"}

    record_export(session, row_count=len(rows), filters={"run_id": run.id}, csv_text=text)
    [export] = list_events(session, event_types=["export"])
    assert '"rows": %d' % len(rows) in export.payload_json


def test_audit_payloads_hold_no_raw_pii(session, run):
    for event in list_events(session):
        for secret in ("Avery", "Strongfit", "Middleton", "@example.com", "555-0101"):
            assert secret not in event.payload_json, (event.event_type, secret)
