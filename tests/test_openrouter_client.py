"""OpenRouter client against mocked HTTP: request shape, result parsing, fallback, retries, errors."""

import json

import httpx
import pytest

from talentsift.config import Settings
from talentsift.llm.base import LLMError
from talentsift.llm.factory import build_client
from talentsift.llm.openrouter_client import OpenRouterClient
from talentsift.schemas import ScreeningOutput

VALID_REPLY = {
    "criteria": [{"criterion_id": 1, "score": 3, "rationale": "Clear SQL use.", "evidence": ["Wrote SQL queries"]}],
    "strengths": ["SQL"],
    "gaps": [],
    "summary": "The applicant writes SQL.",
}


def completion(content: str, *, model="vendor/model-x-2026-01", provider="ProviderCo", cost=0.0012) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "gen-123",
            "object": "chat.completion",
            "created": 1_790_000_000,
            "model": model,
            "provider": provider,
            "choices": [
                {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160, "cost": cost},
        },
    )


class Recorder:
    """httpx MockTransport handler that replays queued responses and records requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def body(self, index: int) -> dict:
        return json.loads(self.requests[index].content)


def make_client(recorder: Recorder) -> OpenRouterClient:
    return OpenRouterClient(
        api_key="sk-or-test",
        model="vendor/model-x",
        http_client=httpx.Client(transport=httpx.MockTransport(recorder)),
        retry_delay_seconds=0,
    )


def test_request_shape_matches_openrouter_structured_outputs():
    recorder = Recorder(completion(json.dumps(VALID_REPLY)))
    make_client(recorder).complete_json("system rules", "user prompt", ScreeningOutput)

    request = recorder.requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-or-test"
    assert request.headers["x-title"] == "TalentSift"

    body = recorder.body(0)
    assert body["model"] == "vendor/model-x"
    assert body["temperature"] == 0
    assert body["messages"] == [
        {"role": "system", "content": "system rules"},
        {"role": "user", "content": "user prompt"},
    ]
    assert body["response_format"]["type"] == "json_schema"
    json_schema = body["response_format"]["json_schema"]
    assert json_schema["name"] == "screening_output"
    assert json_schema["strict"] is True
    assert json_schema["schema"]["additionalProperties"] is False
    assert set(json_schema["schema"]["required"]) == {"criteria", "strengths", "gaps", "summary"}
    assert body["provider"] == {"require_parameters": True}


def test_result_reports_model_provider_tokens_and_cost():
    recorder = Recorder(completion(json.dumps(VALID_REPLY)))
    result = make_client(recorder).complete_json("s", "u", ScreeningOutput)
    assert result.parsed == VALID_REPLY
    assert result.model_requested == "vendor/model-x"
    assert result.model_used == "vendor/model-x-2026-01"
    assert result.provider_used == "ProviderCo"
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (120, 40, 160)
    assert result.cost_usd == pytest.approx(0.0012)
    assert result.used_fallback is False
    assert "api_key" not in json.dumps(result.request)


def test_falls_back_to_plain_json_when_model_rejects_response_format():
    rejected = httpx.Response(
        404, json={"error": {"code": 404, "message": "No endpoints found that can handle the requested parameters."}}
    )
    fenced = "```json\n" + json.dumps(VALID_REPLY) + "\n```"
    recorder = Recorder(rejected, completion(fenced), completion(json.dumps(VALID_REPLY)))
    client = make_client(recorder)

    result = client.complete_json("system rules", "user prompt", ScreeningOutput)
    assert result.used_fallback is True
    assert "No endpoints found" in result.fallback_reason
    assert result.parsed == VALID_REPLY  # code fences are tolerated

    fallback_body = recorder.body(1)
    assert "response_format" not in fallback_body
    assert "provider" not in fallback_body
    assert "JSON schema" in fallback_body["messages"][0]["content"]

    # The client remembers: the next call skips straight to the fallback (one request, not two).
    second = client.complete_json("system rules", "user prompt", ScreeningOutput)
    assert second.used_fallback is True
    assert len(recorder.requests) == 3


def test_retries_once_on_network_failure():
    recorder = Recorder(httpx.ConnectError("connection reset"), completion(json.dumps(VALID_REPLY)))
    result = make_client(recorder).complete_json("s", "u", ScreeningOutput)
    assert result.network_retries == 1
    assert len(recorder.requests) == 2


def test_two_network_failures_raise_llm_error():
    recorder = Recorder(httpx.ConnectError("down"), httpx.ConnectError("still down"))
    with pytest.raises(LLMError) as raised:
        make_client(recorder).complete_json("s", "u", ScreeningOutput)
    assert not raised.value.fatal


def test_bad_key_is_fatal():
    recorder = Recorder(httpx.Response(401, json={"error": {"message": "No auth credentials found"}}))
    with pytest.raises(LLMError) as raised:
        make_client(recorder).complete_json("s", "u", ScreeningOutput)
    assert raised.value.fatal and raised.value.status_code == 401


def test_missing_key_or_model_fails_fast():
    with pytest.raises(LLMError):
        OpenRouterClient(api_key="", model="vendor/model-x")
    with pytest.raises(LLMError):
        OpenRouterClient(api_key="sk", model="")


def test_factory_uses_model_from_settings():
    client = build_client(Settings(llm_provider="openrouter", openrouter_api_key="sk", llm_model="vendor/abc"))
    assert isinstance(client, OpenRouterClient) and client.model == "vendor/abc"
    assert build_client(Settings(llm_provider="fake")).provider == "fake"


def test_moderation_403_is_a_per_applicant_error_not_fatal():
    recorder = Recorder(httpx.Response(403, json={"error": {"message": "Input was flagged by moderation"}}))
    with pytest.raises(LLMError) as raised:
        make_client(recorder).complete_json("s", "u", ScreeningOutput)
    assert raised.value.status_code == 403 and not raised.value.fatal
