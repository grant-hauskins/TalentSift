"""Build the LLM client chosen in settings (or explicitly by the UI)."""

from __future__ import annotations

from talentsift.config import Settings
from talentsift.llm.base import LLMClient
from talentsift.llm.fake_client import FakeLLMClient
from talentsift.llm.openrouter_client import OpenRouterClient


def build_client(settings: Settings, provider: str | None = None) -> LLMClient:
    """Return a ready client. Raises LLMError (fatal) if OpenRouter is chosen but not configured."""
    provider = provider or settings.llm_provider
    if provider == "fake":
        return FakeLLMClient()
    if provider == "openrouter":
        return OpenRouterClient.from_settings(settings)
    raise ValueError(f"Unknown LLM provider: {provider!r}")


def describe_provider(settings: Settings, provider: str | None = None) -> str:
    """One-line description for the UI, e.g. "OpenRouter · vendor/model"."""
    provider = provider or settings.llm_provider
    if provider == "fake":
        return "Offline fake client (deterministic demo backup)"
    model = settings.llm_model or "LLM_MODEL not set"
    key = "key set" if settings.openrouter_api_key else "OPENROUTER_API_KEY missing"
    return f"OpenRouter · {model} · {key}"
