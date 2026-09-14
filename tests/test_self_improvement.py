"""Tests for progressive patch self-improvement before council escalation."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from repo_mgmt.self_improvement import run_self_improvement


def _patch(replacement: str = "<title>New</title>") -> dict[str, Any]:
    return {
        "patchProtocol": "AnchorPatch/v1",
        "changes": [
            {
                "file": "index.html",
                "operation": "replace",
                "anchorBefore": "<title>Old</title>",
                "find": "<title>Old</title>",
                "replace": replacement,
                "rationale": "Update the title.",
            }
        ],
    }


def _response(
    decision: str,
    confidence: int,
    patch: dict[str, Any] | None = None,
    reason: str = "reviewed",
) -> str:
    return json.dumps(
        {
            "decision": decision,
            "confidence": confidence,
            "defects": [],
            "reason": reason,
            "patch": patch or _patch(),
        }
    )


class FakeRouter:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def complete_with_model_async(
        self, model: str, prompt: str, system: str, **kwargs: Any
    ) -> str:
        self.calls.append({"model": model, "prompt": prompt, "system": system, **kwargs})
        return self.responses[len(self.calls) - 1]


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "rms_self_improvement_enabled": True,
        "rms_self_improvement_max_loops": 3,
        "rms_self_improvement_confidence": 85,
        "openrouter_triage_model": "provider/fast",
        "openrouter_secondary_model": "provider/standard",
        "openrouter_primary_model": "provider/strong",
        "rms_engineering_council_architect_model": "provider/architect",
        "rms_max_context_files": 8,
        "rms_max_context_file_bytes": 131_072,
        "rms_max_context_total_bytes": 524_288,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _issue() -> dict[str, Any]:
    return {
        "taskId": "t1",
        "classification": "code_fix",
        "title": "Fix title",
        "description": "Update the title",
        "requiredOutcome": "Use the new title",
        "affectedPaths": ["index.html"],
        "evidence": ["old title present"],
    }


@pytest.mark.asyncio
async def test_accepts_first_loop_when_threshold_is_met(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<title>Old</title>", encoding="utf-8")
    router = FakeRouter([_response("accept", 91)])

    result = await run_self_improvement(
        _issue(), _patch(), tmp_path, "on-brand", _settings(), router
    )

    assert result["accepted"] is True
    assert result["decision"] == "accept"
    assert len(result["loops"]) == 1
    assert router.calls[0]["model"] == "provider/fast"
    assert router.calls[0]["governance_role"] == "OPENROUTER_TRIAGE_MODEL"


@pytest.mark.asyncio
async def test_escalates_progressively_and_keeps_improved_candidate(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<title>Old</title>", encoding="utf-8")
    improved = _patch("<title>Better</title>")
    router = FakeRouter(
        [
            _response("improve", 70, improved, "fixed one defect"),
            _response("accept", 92, improved, "ready"),
        ]
    )

    result = await run_self_improvement(
        _issue(), _patch(), tmp_path, "on-brand", _settings(), router
    )

    assert result["accepted"] is True
    assert [call["model"] for call in router.calls] == [
        "provider/fast",
        "provider/standard",
    ]
    assert result["patch"]["changes"][0]["replace"] == "<title>Better</title>"
    assert result["loops"][0]["candidateChanged"] is True


@pytest.mark.asyncio
async def test_exhausts_three_loops_then_requires_council(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<title>Old</title>", encoding="utf-8")
    router = FakeRouter(
        [
            _response("improve", 60),
            _response("improve", 72),
            _response("accept", 84),
        ]
    )

    result = await run_self_improvement(
        _issue(), _patch(), tmp_path, "on-brand", _settings(), router
    )

    assert result["accepted"] is False
    assert result["decision"] == "escalate_to_council"
    assert [loop["confidence"] for loop in result["loops"]] == [60, 72, 84]
    assert [call["model"] for call in router.calls] == [
        "provider/fast",
        "provider/standard",
        "provider/strong",
    ]
