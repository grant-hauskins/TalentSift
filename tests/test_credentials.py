"""Storing the API key from the app: written to .env, applied at once, never shown in full."""

import os

import pytest
from dotenv import dotenv_values

from talentsift.config import Settings, get_settings
from talentsift.credentials import CredentialError, clear_api_key, key_warning, mask_key, save_api_settings


NAMES = ("OPENROUTER_API_KEY", "LLM_MODEL", "LLM_PROVIDER")


@pytest.fixture
def env_file(tmp_path):
    """The functions under test write os.environ directly, so restore it by hand afterwards."""
    saved = {name: os.environ.pop(name) for name in NAMES if name in os.environ}
    get_settings.cache_clear()
    yield tmp_path / ".env"
    for name in NAMES:
        os.environ.pop(name, None)
    os.environ.update(saved)
    get_settings.cache_clear()


def current() -> Settings:
    """Settings from the process environment only (never the developer's real .env file)."""
    return Settings.from_env(os.environ)


def test_save_writes_env_and_updates_running_settings(env_file):
    env_file.write_text("COST_CAP_USD=1.50\n")
    save_api_settings("sk-or-v1-abc123def456", "openai/gpt-4o-mini", env_path=env_file)
    stored = dotenv_values(env_file)
    assert stored["OPENROUTER_API_KEY"] == "sk-or-v1-abc123def456"
    assert stored["LLM_MODEL"] == "openai/gpt-4o-mini"
    assert stored["LLM_PROVIDER"] == "openrouter"
    assert stored["COST_CAP_USD"] == "1.50"  # other settings are kept
    settings = current()
    assert settings.openrouter_api_key == "sk-or-v1-abc123def456" and settings.llm_provider == "openrouter"
    assert "sk-or-v1" not in repr(settings)


def test_blank_key_keeps_the_stored_one_and_changes_the_model(env_file):
    save_api_settings("sk-or-v1-first", "vendor/model-a", env_path=env_file)
    save_api_settings("", "vendor/model-b", env_path=env_file)
    assert dotenv_values(env_file)["OPENROUTER_API_KEY"] == "sk-or-v1-first"
    assert current().llm_model == "vendor/model-b"


@pytest.mark.parametrize("key, model", [("", "vendor/model"), ("sk-or-v1 abc", "vendor/model"), ("sk-or-v1-abc", " ")])
def test_invalid_input_is_rejected(env_file, key, model):
    with pytest.raises(CredentialError):
        save_api_settings(key, model, env_path=env_file)
    assert not env_file.exists()


def test_clear_removes_the_key_and_falls_back_offline(env_file):
    save_api_settings("sk-or-v1-abc123def456", "vendor/model", env_path=env_file)
    clear_api_key(env_path=env_file)
    assert "OPENROUTER_API_KEY" not in dotenv_values(env_file)
    assert "OPENROUTER_API_KEY" not in os.environ
    assert current().llm_provider == "fake"


def test_mask_and_warning():
    assert mask_key("sk-or-v1-0123456789abcdef") == "sk-or-…cdef"
    assert mask_key("") == "(not set)" and mask_key("short") == "•••••"
    assert key_warning("sk-or-v1-abc") == "" and "sk-or-" in key_warning("sk-proj-abc")
