"""DB initialization, settings parsing, and append-only enforcement at the database level."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DatabaseError
from sqlmodel import select

from talentsift.audit import log_event
from talentsift.config import Settings
from talentsift.db import AppendOnlyError, create_db_engine, init_db
from talentsift.models import Criterion, Role


def test_init_db_creates_all_tables(engine):
    tables = set(inspect(engine).get_table_names())
    assert {
        "role",
        "criterion",
        "applicant",
        "screening_run",
        "evaluation",
        "criterion_score",
        "override",
        "audit_event",
    } <= tables


def test_init_db_is_idempotent(engine):
    init_db(engine)  # second call must not fail (tables and triggers use IF NOT EXISTS)


def test_reserved_word_columns_round_trip(session):
    role = Role(title="Analyst")
    session.add(role)
    session.flush()
    session.add(Criterion(role_id=role.id, name="SQL", type="must_have", weight="high", order=2))
    session.commit()
    stored = session.exec(select(Criterion)).one()
    assert stored.order == 2 and stored.type == "must_have"


def test_audit_event_rejects_sql_update_and_delete(engine, session):
    log_event(session, "export", payload={"rows": 0})
    session.commit()
    with engine.connect() as connection:
        with pytest.raises(DatabaseError, match="append-only"):
            connection.execute(text("UPDATE audit_event SET event_type = 'tampered'"))
        with pytest.raises(DatabaseError, match="append-only"):
            connection.execute(text("DELETE FROM audit_event"))


def test_audit_event_rejects_orm_update_and_delete(session):
    event = log_event(session, "export", payload={"rows": 0})
    session.commit()

    event.event_type = "tampered"
    with pytest.raises(AppendOnlyError):
        session.flush()
    session.rollback()

    session.delete(event)
    with pytest.raises(AppendOnlyError):
        session.flush()
    session.rollback()


def test_unknown_event_type_is_rejected(session):
    with pytest.raises(ValueError):
        log_event(session, "made_up_event")


def test_file_database_is_created_with_parent_folder(tmp_path):
    url = f"sqlite:///{tmp_path / 'nested' / 'talentsift.db'}"
    engine = create_db_engine(url)
    init_db(engine)
    assert (tmp_path / "nested" / "talentsift.db").exists()
    engine.dispose()


def test_settings_from_env_parses_values():
    settings = Settings.from_env(
        {
            "OPENROUTER_API_KEY": "sk-test",
            "LLM_MODEL": "vendor/model",
            "AUTO_REJECT_MODE": "confirm",
            "MASK_GRAD_YEARS": "true",
            "MUST_HAVE_PENALTY": "10",
            "COST_CAP_USD": "0.5",
        }
    )
    assert settings.llm_provider == "openrouter"  # inferred from the key
    assert settings.llm_model == "vendor/model"
    assert settings.auto_reject_mode == "confirm"
    assert settings.mask_grad_years is True
    assert settings.must_have_penalty == 10
    assert settings.cost_cap_usd == 0.5
    assert settings.llm_temperature == 0.0
    assert "sk-test" not in repr(settings)


def test_settings_default_to_offline_fake_client():
    settings = Settings.from_env({})
    assert settings.llm_provider == "fake"
    assert settings.auto_reject_mode == "automatic"
    assert settings.mask_grad_years is False


def test_settings_reject_invalid_mode():
    with pytest.raises(ValueError):
        Settings.from_env({"AUTO_REJECT_MODE": "sometimes"})


def test_env_example_runs_offline_until_a_key_is_added():
    from dotenv import dotenv_values

    from talentsift.config import PROJECT_ROOT

    env = {key: value or "" for key, value in dotenv_values(PROJECT_ROOT / ".env.example").items()}
    assert Settings.from_env(env).llm_provider == "fake"
    env["OPENROUTER_API_KEY"] = "sk-or-test"
    settings = Settings.from_env(env)
    assert settings.llm_provider == "openrouter" and settings.llm_model  # example model slug is filled in


def test_insert_or_replace_cannot_rewrite_an_audit_event(engine, session):
    event = log_event(session, "export", payload={"rows": 1})
    session.commit()
    with engine.connect() as connection:
        with pytest.raises(DatabaseError, match="append-only"):
            connection.execute(
                text(
                    "INSERT OR REPLACE INTO audit_event (id, timestamp, event_type, payload_json) "
                    "VALUES (:id, '2020-01-01', 'export', '{\"rows\": 999}')"
                ),
                {"id": event.id},
            )
