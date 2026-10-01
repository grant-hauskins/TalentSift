"""Provider-agnostic LLM interface.

Business code depends only on `LLMClient.complete_json(system, user, schema) -> LLMResult`.
`call_with_validation` adds the "validate, retry once" loop shared by rubric drafting and screening.

The LLM layer never touches the database: callers log each `Attempt` to the audit trail themselves. That
keeps this package safe to run in worker threads.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

from talentsift.models import utcnow

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMResult:
    """One model reply plus everything the audit log needs about how it was produced."""

    raw_text: str
    parsed: dict[str, Any] | None  # JSON object found in raw_text (not yet schema-validated)
    model_requested: str
    model_used: str
    provider_used: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None  # None when the provider did not report a cost
    used_fallback: bool = False  # True when plain JSON instructions replaced response_format
    fallback_reason: str = ""
    network_retries: int = 0
    latency_ms: int = 0
    request: dict[str, Any] = field(default_factory=dict)  # what was sent; never contains keys


class LLMError(RuntimeError):
    """A call failed after retries. `fatal` means every later call would fail too (bad key, no credit)."""

    def __init__(self, message: str, *, fatal: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.fatal = fatal
        self.status_code = status_code


@runtime_checkable
class LLMClient(Protocol):
    provider: str  # e.g. "openrouter" or "fake"
    model: str  # the model slug requested

    def complete_json(self, system: str, user: str, schema: type[BaseModel]) -> LLMResult:
        """Ask for one JSON object matching `schema`. Raises LLMError on failure."""
        ...


# --- Helpers ---------------------------------------------------------------------------------------

# JSON-schema keywords that some providers reject in strict mode. Pydantic still enforces them locally.
_UNSUPPORTED_KEYWORDS = {
    "title", "default", "examples", "format", "pattern", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "minLength", "maxLength", "minItems", "maxItems",
}  # fmt: skip


def schema_name(schema: type[BaseModel]) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", schema.__name__).lower()


def strict_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Pydantic model -> JSON schema that strict structured-output providers accept.

    Inlines $refs, marks every object `additionalProperties: false` with all properties required, and drops
    keywords some providers reject (local Pydantic validation still enforces them).
    """
    raw = schema.model_json_schema()
    definitions = raw.pop("$defs", {})

    def clean(node: Any) -> Any:
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return clean(copy.deepcopy(definitions[node["$ref"].split("/")[-1]]))
        result: dict[str, Any] = {}
        for key, value in node.items():
            if key in _UNSUPPORTED_KEYWORDS:
                continue
            if key == "properties":  # keys here are field names, not keywords: keep them all
                result[key] = {name: clean(sub) for name, sub in value.items()}
            else:
                result[key] = clean(value)
        if result.get("type") == "object":
            result["additionalProperties"] = False
            result["required"] = list(result.get("properties", {}))
        return result

    return clean(raw)


def json_instructions(schema: type[BaseModel]) -> str:
    """Plain-text instructions used when a model does not support response_format."""
    return (
        "\n\nOutput format: reply with a single JSON object and nothing else (no code fences, no prose). "
        "It must match this JSON schema exactly:\n"
        + json.dumps(strict_json_schema(schema), indent=1)
    )


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object from a reply, tolerating code fences or stray prose around it."""
    if not text:
        return None
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def describe_validation_error(exc: Exception) -> str:
    """Short, model-readable description of what was wrong with a reply."""
    if isinstance(exc, ValidationError):
        parts = []
        for error in exc.errors()[:6]:
            location = ".".join(str(part) for part in error["loc"]) or "(root)"
            parts.append(f"{location}: {error['msg']}")
        return "; ".join(parts)
    return str(exc)


# --- Validate-and-retry loop -------------------------------------------------------------------------

RETRY_NOTE = (
    "\n\nYour previous reply could not be used: {error}\n"
    "Reply again with only the JSON object, following the schema and every rule above."
)


@dataclass
class Attempt:
    """One request/reply round trip, kept so the caller can write it to the audit log."""

    number: int
    system: str
    user: str
    sent_at: datetime
    result: LLMResult | None = None
    call_error: str = ""  # network or API failure (after the client's own retry)
    validation_error: str = ""  # reply arrived but failed schema or semantic checks


@dataclass
class ValidatedCall(Generic[T]):
    value: T | None
    attempts: list[Attempt]
    fatal_error: str = ""  # set when the provider says every later call will fail too

    @property
    def ok(self) -> bool:
        return self.value is not None

    @property
    def last_result(self) -> LLMResult | None:
        for attempt in reversed(self.attempts):
            if attempt.result is not None:
                return attempt.result
        return None

    @property
    def last_error(self) -> str:
        last = self.attempts[-1] if self.attempts else None
        return (last.call_error or last.validation_error) if last else ""

    @property
    def total_tokens(self) -> int:
        return sum(a.result.total_tokens for a in self.attempts if a.result)

    @property
    def total_cost(self) -> float:
        return sum(a.result.cost_usd or 0.0 for a in self.attempts if a.result)


def call_with_validation(
    client: LLMClient,
    system: str,
    user: str,
    schema: type[T],
    *,
    check: Callable[[T], None] | None = None,
    max_attempts: int = 2,
) -> ValidatedCall[T]:
    """Call the model, validate the reply, and retry once with feedback if it is invalid.

    `check` runs semantic rules after schema validation and raises ValueError on a problem.
    A call error (network failure after the client's retry, bad key, ...) ends the loop immediately.
    """
    attempts: list[Attempt] = []
    prompt = user
    for number in range(1, max_attempts + 1):
        attempt = Attempt(number=number, system=system, user=prompt, sent_at=utcnow())
        attempts.append(attempt)
        try:
            result = client.complete_json(system, prompt, schema)
        except LLMError as exc:
            attempt.call_error = str(exc)
            return ValidatedCall(value=None, attempts=attempts, fatal_error=str(exc) if exc.fatal else "")
        attempt.result = result
        try:
            data = result.parsed if result.parsed is not None else extract_json_object(result.raw_text)
            if data is None:
                raise ValueError("the reply was not a JSON object")
            value = schema.model_validate(data)
            if check is not None:
                check(value)
            return ValidatedCall(value=value, attempts=attempts)
        except ValueError as exc:  # pydantic.ValidationError is a ValueError
            attempt.validation_error = describe_validation_error(exc)
            prompt = user + RETRY_NOTE.format(error=attempt.validation_error)
    return ValidatedCall(value=None, attempts=attempts)
