"""An existing v1 database keeps working after the update (T08): missing columns are added in place."""

from sqlalchemy import inspect, text
from sqlmodel import select

from talentsift.db import create_db_engine, init_db, new_session, upgrade_schema
from talentsift.models import CATEGORIES, Criterion, Role

V1_MISSING = {
    "role": ["category_weights_json"],
    "criterion": ["category"],
    "screening_run": ["category_weights_json"],
    "evaluation": ["breakdown_json"],
}
NEW_TABLES = ["candidate_profile", "core_fact", "resume_version"]


def make_v1_database(path):
    """Build today's schema, then strip it back to what the v1 prototype had, with one role in it."""
    engine = create_db_engine(f"sqlite:///{path}")
    init_db(engine)
    with engine.begin() as connection:
        for table in NEW_TABLES:
            connection.execute(text(f"DROP TABLE {table}"))
        for table, columns in V1_MISSING.items():
            for column in columns:
                connection.execute(text(f'ALTER TABLE "{table}" DROP COLUMN "{column}"'))
        connection.execute(text("INSERT INTO role (id, title, summary, posting_text, version, auto_reject_threshold, top_n, created_at, updated_at) "
                                "VALUES (1, 'Old role', '', '', 1, 40, 5, '2026-10-01 00:00:00', '2026-10-01 00:00:00')"))
        connection.execute(text('INSERT INTO criterion (id, role_id, role_version, name, description, type, weight, "order", source, proxy_warning) '
                                "VALUES (1, 1, 1, 'SQL', '', 'must_have', 'high', 0, 'manager', '')"))
    engine.dispose()


def test_v1_database_is_upgraded_in_place_and_old_rows_get_defaults(tmp_path):
    path = tmp_path / "v1.db"
    make_v1_database(path)
    engine = create_db_engine(f"sqlite:///{path}")
    columns = {c["name"] for c in inspect(engine).get_columns("criterion")}
    assert "category" not in columns

    init_db(engine)
    inspector = inspect(engine)
    for table, expected in V1_MISSING.items():
        assert set(expected) <= {c["name"] for c in inspector.get_columns(table)}
    assert set(NEW_TABLES) <= set(inspector.get_table_names())
    with new_session(engine) as session:
        role = session.exec(select(Role)).one()
        assert role.title == "Old role" and role.category_weights == {c: 25 for c in CATEGORIES}
        assert session.exec(select(Criterion)).one().category == "skills"
    engine.dispose()


def test_upgrade_is_a_no_op_on_a_current_database_and_append_only_triggers_survive(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'new.db'}")
    init_db(engine)
    assert upgrade_schema(engine) == []
    init_db(engine)  # safe to run on every start
    with engine.connect() as connection:
        triggers = {row[0] for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))}
        assert {"audit_event_no_update", "audit_event_no_delete"} <= triggers
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    engine.dispose()
