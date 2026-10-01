"""OpenRouter client: the `openai` SDK pointed at https://openrouter.ai/api/v1.

Each request asks for strict JSON-schema output and sets `provider.require_parameters=true`, so OpenRouter
only routes to providers that honor structured outputs. If the model rejects that, the client falls back
to plain JSON instructions (Pydantic still validates the reply) and marks the result `used_fallback`.

Swap models by changing LLM_MODEL in .env. No model slug appears in code.
"""

from __future__ import annotations

import copy
import time
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel

from talentsift.config import Settings
from talentsift.llm.base import (
    LLMError,
    LLMResult,
    extract_json_object,
    json_instructions,
    schema_name,
    strict_json_schema,
)

# Errors worth one retry: dropped connections, timeouts (a subclass of APIConnectionError), 429, 5xx.
_RETRYABLE = (APIConnectionError, RateLimitError, InternalServerError)
# Status codes that mean "every later call will fail too".
_FATAL_STATUS = {401, 402, 403, 404}
# Status codes a provider returns when it cannot honor response_format / require_parameters.
_STRUCTURED_REJECTED_STATUS = {400, 404, 422}


class _StructuredOutputRejected(Exception):
    pass


class OpenRouterClient:
    provider = "openrouter"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        temperature: float = 0.0,
        timeout_seconds: float = 60.0,
        max_tokens: int = 4000,
        retry_delay_seconds: float = 2.0,
        http_client: httpx.Client | None = None,
    ):
        if not api_key:
            raise LLMError("OPENROUTER_API_KEY is not set. Add it to .env or the hosting secrets.", fatal=True)
        if not model:
            raise LLMError("LLM_MODEL is not set. Add an OpenRouter model slug to .env.", fatal=True)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.retry_delay_seconds = retry_delay_seconds
        # max_retries=0: we retry once ourselves so the audit log can show it.
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
            http_client=http_client,
            default_headers={"X-Title": "TalentSift"},  # app attribution shown on openrouter.ai
        )
        # None = not tried yet; False = this model rejected structured outputs, use the fallback directly.
        self._structured_supported: bool | None = None

    @classmethod
    def from_settings(cls, settings: Settings, **overrides: Any) -> "OpenRouterClient":
        options: dict[str, Any] = dict(
            api_key=settings.openrouter_api_key,
            model=settings.llm_model,
            base_url=settings.openrouter_base_url,
            temperature=settings.llm_temperature,
            timeout_seconds=settings.llm_timeout_seconds,
            max_tokens=settings.llm_max_tokens,
        )
        options.update(overrides)
        return cls(**options)

    # --- Request building ------------------------------------------------------------------------

    def build_request(self, system: str, user: str, schema: type[BaseModel], *, structured: bool) -> dict:
        """Keyword arguments for `chat.completions.create`. Public so tests can inspect them."""
        request: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system if structured else system + json_instructions(schema)},
                {"role": "user", "content": user},
            ],
        }
        if structured:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name(schema), "strict": True, "schema": strict_json_schema(schema)},
            }
            request["extra_body"] = {"provider": {"require_parameters": True}, "usage": {"include": True}}
        else:
            request["extra_body"] = {"usage": {"include": True}}
        return request

    # --- Calls -----------------------------------------------------------------------------------

    def complete_json(self, system: str, user: str, schema: type[BaseModel]) -> LLMResult:
        if self._structured_supported is not False:
            try:
                result = self._send(self.build_request(system, user, schema, structured=True), structured=True)
                self._structured_supported = True
                return result
            except _StructuredOutputRejected as exc:
                reason = str(exc)
            result = self._send(self.build_request(system, user, schema, structured=False), structured=False)
            self._structured_supported = False  # the fallback worked, so the problem was response_format
            result.fallback_reason = reason
            return result
        result = self._send(self.build_request(system, user, schema, structured=False), structured=False)
        result.fallback_reason = "this model rejected structured outputs earlier in the session"
        return result

    def _send(self, request: dict, *, structured: bool) -> LLMResult:
        retries = 0
        started = time.monotonic()
        while True:
            try:
                completion = self._client.chat.completions.create(**request)
                break
            except _RETRYABLE as exc:
                if retries == 0:
                    retries = 1
                    time.sleep(self.retry_delay_seconds)
                    continue
                raise LLMError(f"OpenRouter request failed after one retry: {_describe(exc)}") from exc
            except APIStatusError as exc:
                if structured and exc.status_code in _STRUCTURED_REJECTED_STATUS:
                    raise _StructuredOutputRejected(_describe(exc)) from exc
                raise LLMError(
                    f"OpenRouter returned {exc.status_code}: {_describe(exc)}",
                    fatal=exc.status_code in _FATAL_STATUS,
                    status_code=exc.status_code,
                ) from exc
        return self._to_result(completion, request, structured=structured, retries=retries, started=started)

    def _to_result(self, completion: Any, request: dict, *, structured: bool, retries: int, started: float) -> LLMResult:
        data = completion.model_dump()  # keeps OpenRouter extras such as "provider" and usage.cost
        choices = data.get("choices") or [{}]
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):  # some providers return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        usage = data.get("usage") or {}
        cost = usage.get("cost")
        return LLMResult(
            raw_text=content,
            parsed=extract_json_object(content),
            model_requested=self.model,
            model_used=data.get("model") or self.model,
            provider_used=data.get("provider") or "unknown",
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            cost_usd=float(cost) if cost is not None else None,
            used_fallback=not structured,
            network_retries=retries,
            latency_ms=int((time.monotonic() - started) * 1000),
            request=_redacted(request),
        )


def _describe(exc: Exception) -> str:
    message = getattr(exc, "message", None) or str(exc)
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict) and error.get("message"):
            message = str(error["message"])
    return message[:500]


def _redacted(request: dict) -> dict:
    """Copy of the request for the audit log (the API key lives in headers, never in the body)."""
    return copy.deepcopy(request)
