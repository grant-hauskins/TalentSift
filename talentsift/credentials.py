"""Store the OpenRouter API key and model from inside the app.

The key is written to the project's `.env` file (gitignored, the same file `Settings.from_env` reads), so it
survives restarts and stays out of the database, the audit log, and git. The running process picks up the
change at once: the value is also set in `os.environ` and the cached settings are cleared.

On Streamlit Community Cloud the disk resets on reboot; use the hosting secrets manager there instead.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import set_key, unset_key

from talentsift.config import PROJECT_ROOT, get_settings

ENV_PATH = PROJECT_ROOT / ".env"
KEY_VAR = "OPENROUTER_API_KEY"
MODEL_VAR = "LLM_MODEL"
PROVIDER_VAR = "LLM_PROVIDER"
KEY_PREFIX = "sk-or-"


class CredentialError(ValueError):
    """The key or model could not be saved (bad input or unwritable file)."""


def mask_key(key: str) -> str:
    """Show enough of a key to recognize it, never the whole thing: 'sk-or-…a1b2'."""
    key = key.strip()
    if not key:
        return "(not set)"
    if len(key) <= 10:
        return "•" * len(key)
    return f"{key[:6]}…{key[-4:]}"


def key_warning(key: str) -> str:
    """A soft warning for keys that do not look like OpenRouter keys (saving is still allowed)."""
    key = key.strip()
    if key and not key.startswith(KEY_PREFIX):
        return f"OpenRouter keys usually start with '{KEY_PREFIX}'. Double-check you pasted the right key."
    return ""


def _write(env_path: Path, name: str, value: str) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.touch(mode=0o600)
    try:
        set_key(str(env_path), name, value, quote_mode="never")
    except OSError as exc:
        raise CredentialError(f"Could not write {env_path.name}: {exc}") from exc
    os.environ[name] = value


def save_api_settings(api_key: str, model: str, *, env_path: Path | None = None, use_openrouter: bool = True) -> None:
    """Save the key and model to `.env` and apply them to the running app.

    An empty `api_key` keeps the key already stored, so the model can be changed without re-pasting it.
    """
    env_path = env_path or ENV_PATH
    api_key, model = api_key.strip(), model.strip()
    if any(ch.isspace() for ch in api_key):
        raise CredentialError("The API key cannot contain spaces or line breaks.")
    if not model:
        raise CredentialError("Enter a model slug, for example 'openai/gpt-4o-mini'.")
    if not api_key and not os.environ.get(KEY_VAR, "").strip():
        raise CredentialError("Enter an API key.")
    if api_key:
        _write(env_path, KEY_VAR, api_key)
    _write(env_path, MODEL_VAR, model)
    if use_openrouter:
        _write(env_path, PROVIDER_VAR, "openrouter")
    get_settings.cache_clear()


def clear_api_key(*, env_path: Path | None = None) -> None:
    """Remove the stored key and fall back to the offline client."""
    env_path = env_path or ENV_PATH
    if env_path.exists():
        unset_key(str(env_path), KEY_VAR)
        unset_key(str(env_path), PROVIDER_VAR)
    os.environ.pop(KEY_VAR, None)
    os.environ.pop(PROVIDER_VAR, None)
    get_settings.cache_clear()
