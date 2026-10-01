"""Database engine, schema creation, and append-only enforcement.

Append-only tables (`audit_event`, `override`) are protected twice:
1. SQLite triggers abort any UPDATE or DELETE, whatever code issues it.
2. An ORM `before_flush` hook rejects edits or deletes of those objects before SQL is sent.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from talentsift import models

APPEND_ONLY_TABLES = ("audit_event", "override")


class AppendOnlyError(RuntimeError):
    """Raised when code tries to modify or delete an append-only record."""


def _trigger_sql(table: str) -> list[str]:
    message = f"{table} is append-only"
    return [
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{message}: updates are not allowed'); END;",
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{message}: deletes are not allowed'); END;",
    ]


def create_db_engine(database_url: str) -> Engine:
    """Create an engine. SQLite gets thread-safe settings because Streamlit runs scripts in threads."""
    kwargs: dict = {}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if database_url in ("sqlite://", "sqlite:///:memory:"):
            # One shared in-memory database for every connection (used by tests).
            kwargs["poolclass"] = StaticPool
        else:
            Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, **kwargs)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create tables (if missing) and install the append-only triggers."""
    SQLModel.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            for table in APPEND_ONLY_TABLES:
                for statement in _trigger_sql(table):
                    connection.execute(text(statement))


def new_session(engine: Engine) -> Session:
    """A session whose objects stay readable after commit (convenient in Streamlit pages)."""
    return Session(engine, expire_on_commit=False)


@event.listens_for(OrmSession, "before_flush")
def _block_append_only_changes(session, _flush_context, _instances) -> None:
    """Refuse ORM updates or deletes of audit events and overrides."""
    protected = (models.AuditEvent, models.Override)
    for obj in session.deleted:
        if isinstance(obj, protected):
            raise AppendOnlyError(f"{type(obj).__name__} records cannot be deleted")
    for obj in session.dirty:
        if isinstance(obj, protected) and session.is_modified(obj):
            raise AppendOnlyError(f"{type(obj).__name__} records cannot be changed")
