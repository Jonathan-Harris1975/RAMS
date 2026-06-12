"""Tests for bounded OpenRouter routing and accounting."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from repo_mgmt.model_router import ModelError, ModelRouter


def _response(
    status_code: int, body: str = "{}", headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=body.encode(),
        headers=headers,
        request=httpx.Request("POST", "https://example.test/chat/completions"),
    )


def _success(content: str = "ok", model: str = "actual/model") -> httpx.Response:
    return _response(
        200,
        '{"model":"%s","choices":[{"message":{"content":"%s"}}],'
        '"usage":{"prompt_tokens":10,"completion_tokens":5,"cost":0.0001}}'
        % (model, content),
    )


class FakeClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> httpx.Response:
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


class FakeAsyncClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> httpx.Response:
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


def _router_with_sync(
    settings, responses: list[httpx.Response]
) -> tuple[ModelRouter, FakeClient]:
    router = ModelRouter(settings)
    client = FakeClient(responses)
    router._sync_client = client  # type: ignore[assignment]
    return router, client


def test_429_triggers_secondary_fallback(settings) -> None:
    router, client = _router_with_sync(
        settings, [_response(429, "rate limited"), _success("secondary-ok")]
    )
    assert router.complete("prompt") == "secondary-ok"
    assert [call["json"]["model"] for call in client.calls] == [
        settings.openrouter_primary_model,
        settings.openrouter_secondary_model,
    ]


def test_5xx_triggers_secondary_fallback(settings) -> None:
    router, client = _router_with_sync(
        settings, [_response(503, "unavailable"), _success("fallback")]
    )
    assert router.complete("prompt") == "fallback"
    assert len(client.calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403])
def test_non_retryable_4xx_does_not_fallback(settings, status: int) -> None:
    router, client = _router_with_sync(settings, [_response(status, "client error")])
    with pytest.raises(ModelError) as exc_info:
        router.complete("prompt")
    assert exc_info.value.status_code == status
    assert len(client.calls) == 1


def test_json_mode_adds_response_format_and_provider_routing(settings) -> None:
    router, client = _router_with_sync(settings, [_success("json-ok")])
    assert router.complete("prompt", json_mode=True) == "json-ok"
    payload = client.calls[0]["json"]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["provider"]["sort"] == "price"
    assert payload["provider"]["require_parameters"] is True


def test_json_mode_provider_rejection_retries_same_model_without_format(
    settings,
) -> None:
    router, client = _router_with_sync(
        settings,
        [
            _response(400, "response_format is not supported by this model"),
            _success("plain-ok"),
        ],
    )
    assert router.complete("prompt", json_mode=True) == "plain-ok"
    assert "response_format" in client.calls[0]["json"]
    assert "response_format" not in client.calls[1]["json"]
    assert client.calls[0]["json"]["model"] == client.calls[1]["json"]["model"]


@pytest.mark.asyncio
async def test_async_complete_reuses_async_client_and_falls_back(settings) -> None:
    router = ModelRouter(settings)
    client = FakeAsyncClient(
        [_response(503, "unavailable"), _success("async-fallback")]
    )
    router._async_client = client  # type: ignore[assignment]
    assert await router.complete_async("prompt") == "async-fallback"
    assert [call["json"]["model"] for call in client.calls] == [
        settings.openrouter_primary_model,
        settings.openrouter_secondary_model,
    ]


def test_token_cap_and_usage_accounting(settings) -> None:
    router, client = _router_with_sync(settings, [_success("ok")])
    router.start_run("run-1")
    assert router.complete("prompt", max_tokens=99999) == "ok"
    assert client.calls[0]["json"]["max_tokens"] == settings.rms_primary_max_tokens
    usage = router.usage_summary()
    assert usage["runId"] == "run-1"
    assert usage["requests"] == 1
    assert usage["promptTokens"] == 10
    assert usage["completionTokens"] == 5
    assert usage["cost"] == 0.0001


def test_retry_after_is_captured_on_busy_response(settings) -> None:
    router, _ = _router_with_sync(
        settings,
        [_response(429, "busy", {"Retry-After": "7"}), _response(429, "busy")],
    )
    with pytest.raises(ModelError):
        router.complete("prompt")
    assert router.usage_summary()["fallbacks"] == 1


def test_nested_usage_details_and_provider_are_accounted(settings) -> None:
    response = _response(
        200,
        '{"model":"actual/model","provider":"provider-x",'
        '"choices":[{"message":{"content":"ok"}}],'
        '"usage":{"prompt_tokens":20,"completion_tokens":8,"cost":0.0002,'
        '"prompt_tokens_details":{"cached_tokens":7},'
        '"completion_tokens_details":{"reasoning_tokens":3}}}',
    )
    router, _ = _router_with_sync(settings, [response])
    router.start_run("run-provider")
    assert router.complete("prompt") == "ok"
    usage = router.usage_summary()
    assert usage["cachedTokens"] == 7
    assert usage["reasoningTokens"] == 3
    assert usage["providers"] == {"provider-x": 1}
