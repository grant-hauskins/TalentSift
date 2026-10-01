"""LLM layer: a provider-agnostic interface, the OpenRouter client, and a deterministic fake client."""

from talentsift.llm.base import (
    Attempt,
    LLMClient,
    LLMError,
    LLMResult,
    ValidatedCall,
    call_with_validation,
    extract_json_object,
    strict_json_schema,
)
from talentsift.llm.factory import build_client, describe_provider

__all__ = [
    "Attempt",
    "LLMClient",
    "LLMError",
    "LLMResult",
    "ValidatedCall",
    "build_client",
    "call_with_validation",
    "describe_provider",
    "extract_json_object",
    "strict_json_schema",
]
