"""Shared pytest fixtures: an in-memory database and offline settings for every test."""

from __future__ import annotations

import pytest

from talentsift.config import Settings
from talentsift.db import create_db_engine, init_db, new_session


@pytest.fixture
def settings() -> Settings:
    """Offline, deterministic settings. Tests never read the developer's .env file."""
    return Settings(llm_provider="fake", database_url="sqlite://", llm_concurrency=2)


@pytest.fixture
def engine():
    engine = create_db_engine("sqlite://")
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    with new_session(engine) as session:
        yield session
