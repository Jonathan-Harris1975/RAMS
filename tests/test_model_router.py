"""Tests for OpenRouter fallback behaviour."""

from __future__ import annotations

import httpx
import pytest

from repo_mgmt.model_router import ModelError, ModelRouter


def _response(status_code: int, body: str = "{}") -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=body.encode(),
        request=httpx.Request("POST", "https://example.test/chat/completions"),
    )


def _success(content: str = "ok") -> httpx.Response:
    return _response(200, f'{{"choices":[{{"message":{{"content":"{content}"}}}}]}}')


def test_429_triggers_secondary_fallback(settings, monkeypatch) -> None:
    calls: list[str] = []
    responses = [_response(429, "rate limited"), _success("secondary-ok")]

    def fake_post(url, headers, json, timeout):
        calls.append(json["model"])
        return responses.pop(0)

    monkeypatch.setattr(httpx, "post", fake_post)
    router = ModelRouter(settings)
    assert router.complete("prompt") == "secondary-ok"
    assert calls == [
        settings.openrouter_primary_model,
        settings.openrouter_secondary_model,
    ]


def test_5xx_triggers_secondary_fallback(settings, monkeypatch) -> None:
    calls: list[str] = []
    responses = [_response(503, "unavailable"), _success("fallback")]

    def fake_post(url, headers, json, timeout):
        calls.append(json["model"])
        return responses.pop(0)

    monkeypatch.setattr(httpx, "post", fake_post)
    router = ModelRouter(settings)
    assert router.complete("prompt") == "fallback"
    assert calls == [
        settings.openrouter_primary_model,
        settings.openrouter_secondary_model,
    ]


@pytest.mark.parametrize("status", [400, 401, 403])
def test_non_retryable_4xx_does_not_fallback(
    settings, monkeypatch, status: int
) -> None:
    calls: list[str] = []

    def fake_post(url, headers, json, timeout):
        calls.append(json["model"])
        return _response(status, "client error")

    monkeypatch.setattr(httpx, "post", fake_post)
    router = ModelRouter(settings)
    with pytest.raises(ModelError) as exc_info:
        router.complete("prompt")
    assert exc_info.value.status_code == status
    assert calls == [settings.openrouter_primary_model]

@pytest.mark.asyncio
async def test_async_complete_uses_async_client_and_fallback(settings, monkeypatch) -> None:
    calls: list[str] = []
    responses = [_response(503, "unavailable"), _success("async-fallback")]

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object]) -> httpx.Response:
            calls.append(str(json["model"]))
            assert headers["HTTP-Referer"] == "repo-management-suite"
            return responses.pop(0)

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    router = ModelRouter(settings)
    assert await router.complete_async("prompt") == "async-fallback"
    assert calls == [
        settings.openrouter_primary_model,
        settings.openrouter_secondary_model,
    ]
