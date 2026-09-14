from __future__ import annotations

from repo_mgmt.context_resilience import (
    add_openrouter_context_compression,
    deterministic_compact_payload,
    provider_order,
)
from repo_mgmt.model_router import ModelRouter


def test_provider_order_keeps_primary_first_and_direct_last() -> None:
    assert provider_order(
        "leanctx", "context_gateway,headroom,openrouter,deterministic,direct"
    ) == (
        "leanctx",
        "context_gateway",
        "headroom",
        "openrouter",
        "deterministic",
        "direct",
    )
    assert provider_order("context_gateway", "context_gateway,headroom") == (
        "context_gateway",
        "headroom",
        "openrouter",
        "deterministic",
        "direct",
    )


def test_primary_proxy_selection_is_environment_driven(settings) -> None:
    settings.rms_leanctx_base_url = "http://leanctx:4444/v1"
    settings.rms_leanctx_api_key = "lean-token"
    settings.rms_context_gateway_base_url = "http://context-gateway:8080/v1"
    settings.rms_context_gateway_api_key = "gateway-token"
    router = ModelRouter(settings)
    headers, payload = router._request_parts("test/model", "prompt", "", 100, False, 0.0)

    first = next(
        router._context_attempts(headers, payload, model="test/model", exact_context=False)
    )
    assert first[0] == "leanctx"
    assert first[1] == "http://leanctx:4444/v1/chat/completions"
    assert first[2]["Authorization"] == "Bearer lean-token"

    settings.rms_context_primary_provider = "context_gateway"
    second_router = ModelRouter(settings)
    headers, payload = second_router._request_parts(
        "test/model", "prompt", "", 100, False, 0.0
    )
    first = next(
        second_router._context_attempts(
            headers, payload, model="test/model", exact_context=False
        )
    )
    assert first[0] == "context_gateway"
    assert first[1] == "http://context-gateway:8080/v1/chat/completions"
    assert first[2]["Authorization"] == "Bearer gateway-token"


def test_openrouter_context_plugin_is_added_once() -> None:
    payload = {"model": "test/model", "messages": [{"role": "user", "content": "x"}]}
    once = add_openrouter_context_compression(payload)
    twice = add_openrouter_context_compression(once)
    assert twice["plugins"] == [{"id": "context-compression"}]


def test_deterministic_fallback_preserves_system_and_latest_message() -> None:
    payload = {
        "messages": [
            {"role": "system", "content": "system rules"},
            {"role": "assistant", "content": "x" * 20_000},
            {"role": "user", "content": "latest request"},
        ]
    }
    compacted = deterministic_compact_payload(payload, max_chars=9_000)
    assert compacted["messages"][0]["content"] == "system rules"
    assert compacted["messages"][-1]["content"] == "latest request"
    assert "locally compacted" in compacted["messages"][1]["content"]
