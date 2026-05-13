"""Tests for OpenRouter fallback behaviour."""
from __future__ import annotations

import httpx
import pytest

from repo_mgmt.model_router import ModelError, ModelRouter


def _response(status_code: int, body: str = '{}') -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=body.encode(),
        request=httpx.Request('POST', 'https://example.test/chat/completions'),
    )


def _success(content: str = 'ok') -> httpx.Response:
    return _response(200, f'{{"choices":[{{"message":{{"content":"{content}"}}}}]}}')


def test_429_triggers_secondary_fallback(settings, monkeypatch) -> None:
    calls: list[str] = []
    responses = [_response(429, 'rate limited'), _success('secondary-ok')]

    def fake_post(url, headers, json, timeout):
        calls.append(json['model'])
        return responses.pop(0)

    monkeypatch.setattr(httpx, 'post', fake_post)
    router = ModelRouter(settings)
    assert router.complete('prompt') == 'secondary-ok'
    assert calls == [settings.openrouter_primary_model, settings.openrouter_secondary_model]


def test_5xx_triggers_secondary_fallback(settings, monkeypatch) -> None:
    calls: list[str] = []
    responses = [_response(503, 'unavailable'), _success('fallback')]

    def fake_post(url, headers, json, timeout):
        calls.append(json['model'])
        return responses.pop(0)

    monkeypatch.setattr(httpx, 'post', fake_post)
    router = ModelRouter(settings)
    assert router.complete('prompt') == 'fallback'
    assert calls == [settings.openrouter_primary_model, settings.openrouter_secondary_model]


@pytest.mark.parametrize('status', [400, 401, 403])
def test_non_retryable_4xx_does_not_fallback(settings, monkeypatch, status: int) -> None:
    calls: list[str] = []

    def fake_post(url, headers, json, timeout):
        calls.append(json['model'])
        return _response(status, 'client error')

    monkeypatch.setattr(httpx, 'post', fake_post)
    router = ModelRouter(settings)
    with pytest.raises(ModelError) as exc_info:
        router.complete('prompt')
    assert exc_info.value.status_code == status
    assert calls == [settings.openrouter_primary_model]
