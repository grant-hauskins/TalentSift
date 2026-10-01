"""Application settings.

Settings come from environment variables. A local `.env` file is loaded first (copy `.env.example`).
On Streamlit Community Cloud the secrets manager supplies the same keys (see `ui.load_streamlit_secrets`).

Business logic never reads `os.environ` directly. Every function that needs a setting accepts a
`Settings` object, so tests can pass their own values instead of patching the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

LLM_PROVIDERS = ("openrouter", "fake")
AUTO_REJECT_MODES = ("automatic", "confirm")

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in _TRUE_VALUES


def _as_int(value: str | None, default: int) -> int:
    return default if value is None or value.strip() == "" else int(value)


def _as_float(value: str | None, default: float) -> float:
    return default if value is None or value.strip() == "" else float(value)


def _absolute_sqlite_url(url: str) -> str:
    """Resolve a relative SQLite path against the project root, so scripts work from any folder."""
    prefix = "sqlite:///"
    if url.startswith(prefix) and url != "sqlite:///:memory:":
        path = Path(url.removeprefix(prefix))
        if not path.is_absolute():
            return f"{prefix}{PROJECT_ROOT / path}"
    return url


@dataclass(frozen=True)
class Settings:
    """All tunable settings in one place. Defaults are safe for a local, offline demo."""

    # --- AI provider -------------------------------------------------------------------------
    llm_provider: str = "fake"  # "openrouter" or "fake" (deterministic offline client)
    llm_model: str = ""  # OpenRouter model slug, e.g. set LLM_MODEL in .env. Never hardcoded.
    openrouter_api_key: str = field(default="", repr=False)  # repr=False keeps the key out of logs
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Temperature is fixed at 0 for repeatable results. It is deliberately not read from the env.
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 60.0
    llm_max_tokens: int = 4000  # upper bound on the model's reply length
    llm_concurrency: int = 4  # parallel LLM calls during a screening run
    cost_cap_usd: float = 2.00  # per screening batch; the run stops cleanly when reached

    # --- Screening policy --------------------------------------------------------------------
    auto_reject_mode: str = "automatic"  # "automatic" (team decision) or "confirm"
    must_have_penalty: float = 15.0  # points subtracted per must-have scored 0-1
    max_resume_chars: int = 24_000  # longer resumes are truncated and routed to needs_review

    # --- Privacy -----------------------------------------------------------------------------
    mask_grad_years: bool = False  # mask years in the education section when true

    # --- Storage and demo --------------------------------------------------------------------
    database_url: str = f"sqlite:///{DATA_DIR / 'talentsift.db'}"
    upload_dir: Path = DATA_DIR / "resumes" / "uploads"  # copies of uploaded files (gitignored)
    prompts_dir: Path = PROJECT_ROOT / "prompts"
    auto_seed_demo: bool = False  # load sample roles and resumes when the database is empty

    def __post_init__(self) -> None:
        if self.llm_provider not in LLM_PROVIDERS:
            raise ValueError(f"LLM_PROVIDER must be one of {LLM_PROVIDERS}, got {self.llm_provider!r}")
        if self.auto_reject_mode not in AUTO_REJECT_MODES:
            raise ValueError(
                f"AUTO_REJECT_MODE must be one of {AUTO_REJECT_MODES}, got {self.auto_reject_mode!r}"
            )
        if self.llm_concurrency < 1:
            raise ValueError("LLM_CONCURRENCY must be at least 1")
        if self.must_have_penalty < 0:
            raise ValueError("MUST_HAVE_PENALTY cannot be negative")

    def with_overrides(self, **changes) -> "Settings":
        """Return a copy with some values changed (handy in tests and in the UI)."""
        return replace(self, **changes)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Build settings from a mapping (defaults to `os.environ` after loading `.env`)."""
        if env is None:
            load_dotenv(PROJECT_ROOT / ".env", override=False)
            env = os.environ

        api_key = env.get("OPENROUTER_API_KEY", "").strip()
        # Default provider: OpenRouter when a key is present, otherwise the offline fake client.
        provider = env.get("LLM_PROVIDER", "").strip().lower() or ("openrouter" if api_key else "fake")
        defaults = cls()
        return cls(
            llm_provider=provider,
            llm_model=env.get("LLM_MODEL", "").strip(),
            openrouter_api_key=api_key,
            openrouter_base_url=env.get("OPENROUTER_BASE_URL", "").strip() or defaults.openrouter_base_url,
            llm_timeout_seconds=_as_float(env.get("LLM_TIMEOUT_SECONDS"), defaults.llm_timeout_seconds),
            llm_max_tokens=_as_int(env.get("LLM_MAX_TOKENS"), defaults.llm_max_tokens),
            llm_concurrency=_as_int(env.get("LLM_CONCURRENCY"), defaults.llm_concurrency),
            cost_cap_usd=_as_float(env.get("COST_CAP_USD"), defaults.cost_cap_usd),
            auto_reject_mode=env.get("AUTO_REJECT_MODE", "").strip().lower() or defaults.auto_reject_mode,
            must_have_penalty=_as_float(env.get("MUST_HAVE_PENALTY"), defaults.must_have_penalty),
            max_resume_chars=_as_int(env.get("MAX_RESUME_CHARS"), defaults.max_resume_chars),
            mask_grad_years=_as_bool(env.get("MASK_GRAD_YEARS"), defaults.mask_grad_years),
            database_url=_absolute_sqlite_url(env.get("DATABASE_URL", "").strip() or defaults.database_url),
            upload_dir=Path(env.get("UPLOAD_DIR", "").strip() or defaults.upload_dir),
            prompts_dir=Path(env.get("PROMPTS_DIR", "").strip() or defaults.prompts_dir),
            auto_seed_demo=_as_bool(env.get("AUTO_SEED_DEMO"), defaults.auto_seed_demo),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings for the running app, read once per process."""
    return Settings.from_env()
