"""Shared Streamlit helpers: settings, database session, banner, provider picker, and small renderers.

Pages stay thin: they call package functions and use these helpers to display results consistently.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

import streamlit as st
from sqlmodel import Session

from talentsift.config import Settings, get_settings
from talentsift.db import create_db_engine, init_db, new_session
from talentsift.llm import LLMClient, LLMError, build_client, describe_provider
from talentsift.models import (
    STATUS_AUTO_REJECTED,
    STATUS_NEEDS_REVIEW,
    STATUS_NOT_SHORTLISTED,
    STATUS_PENDING_AUTO_REJECT,
    STATUS_SHORTLISTED,
)

BANNER = "AI recommendations and auto-rejections are logged and reversible. Review results before contacting applicants."

STATUS_LABELS = {
    STATUS_SHORTLISTED: "Shortlisted",
    STATUS_NOT_SHORTLISTED: "Not shortlisted",
    STATUS_AUTO_REJECTED: "Auto-rejected",
    STATUS_PENDING_AUTO_REJECT: "Proposed auto-reject",
    STATUS_NEEDS_REVIEW: "Needs review",
}
STATUS_ICONS = {
    STATUS_SHORTLISTED: "🟢",
    STATUS_NOT_SHORTLISTED: "⚪",
    STATUS_AUTO_REJECTED: "🔴",
    STATUS_PENDING_AUTO_REJECT: "🟠",
    STATUS_NEEDS_REVIEW: "🟡",
}
PARSE_ICONS = {"parsed": "✅", "partial": "⚠️", "needs_ocr": "🖼️", "error": "❌", "unsupported": "🚫"}
PROVIDER_LABELS = {"openrouter": "OpenRouter (real model)", "fake": "Offline fake client (demo backup)"}


# --- Settings and database ---------------------------------------------------------------------------


def load_streamlit_secrets() -> None:
    """Copy root-level Streamlit secrets into the environment (Streamlit Community Cloud)."""
    try:
        secrets = {key: st.secrets[key] for key in st.secrets}
    except Exception:  # no secrets.toml when running locally
        return
    for key, value in secrets.items():
        if isinstance(value, (str, int, float, bool)) and key not in os.environ:
            os.environ[key] = str(value)


def settings() -> Settings:
    load_streamlit_secrets()
    return get_settings()


@st.cache_resource(show_spinner=False)
def _engine(database_url: str):
    engine = create_db_engine(database_url)
    init_db(engine)
    return engine


@contextmanager
def db_session() -> Iterator[Session]:
    with new_session(_engine(settings().database_url)) as session:
        yield session


# --- Layout ------------------------------------------------------------------------------------------


def banner() -> None:
    """The persistent disclaimer shown at the top of every page."""
    st.info(BANNER, icon="⚖️")


def page_header(title: str, caption: str | None = None) -> None:
    """Every page starts with the banner and its title. Call this first on each page."""
    banner()
    st.title(title)
    if caption:
        st.caption(caption)


def sidebar_status(current: Settings) -> None:
    """Global AI provider picker and policy summary (rendered by app.py on every page)."""
    with st.sidebar:
        st.radio(
            "AI provider",
            options=list(PROVIDER_LABELS),
            index=list(PROVIDER_LABELS).index(current.llm_provider),
            format_func=PROVIDER_LABELS.get,
            key="llm_provider",
            help="Switch to the offline client if the network or API key is unavailable. Every run records which one it used.",
        )
        provider = selected_provider()
        st.caption(describe_provider(current, provider))
        if provider == "openrouter" and not (current.openrouter_api_key and current.llm_model):
            st.error("Set OPENROUTER_API_KEY and LLM_MODEL in .env (or hosting secrets), or use the offline client.")
        st.caption(
            f"Auto-reject mode: **{current.auto_reject_mode}** · must-have penalty: {current.must_have_penalty:g} pts · "
            f"cost cap: ${current.cost_cap_usd:.2f}/batch"
        )


def selected_provider() -> str:
    return st.session_state.get("llm_provider") or settings().llm_provider


def get_client() -> LLMClient | None:
    """Build the selected client, showing a clear error instead of raising."""
    try:
        return build_client(settings(), selected_provider())
    except LLMError as exc:
        st.error(str(exc))
        return None


# --- Formatting --------------------------------------------------------------------------------------


def status_label(status: str) -> str:
    return f"{STATUS_ICONS.get(status, '')} {STATUS_LABELS.get(status, status)}".strip()


def fmt_time(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fmt_score(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"
