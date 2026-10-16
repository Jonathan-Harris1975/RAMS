"""Tests for conditional RAMS engineering-council escalation."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from repo_mgmt.engineering_council import run_engineering_council


def _response(decision: str, confidence: int, reason: str = "reviewed") -> str:
    return json.dumps(
        {
            "decision": decision,
            "confidence": confidence,
            "defects": [],
            "reason": reason,
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
        "rms_engineering_council_enabled": True,
        "rms_engineering_council_architect_model": "openai/gpt-5.6-sol",
        "rms_engineering_council_specialist_model": "anthropic/claude-sonnet-5",
        "rms_engineering_council_chair_model": "anthropic/claude-opus-5",
        "rms_engineering_council_expert_enabled": False,
        "rms_engineering_council_expert_justification_id": "",
        "rms_engineering_council_review_confidence": 85,
        "rms_engineering_council_chair_confidence": 85,
        "rms_engineering_council_near_threshold_tolerance_percent": 5,
        "rms_model_governance_premium_approvals": {},
        "rms_model_governance_premium_approval_expiries": {},
        "rms_model_governance_premium_roles": set(),
        "rms_model_governance_premium_models": set(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_standard_consensus_approves_without_premium_chair() -> None:
    router = FakeRouter([_response("approve", 91), _response("approve", 90)])

    result = await run_engineering_council({}, {}, _settings(), router)

    assert result["decision"] == "approve_micro_surgery"
    assert result["route"] == "standard-consensus"
    assert len(router.calls) == 2


@pytest.mark.asyncio
async def test_confident_rejection_does_not_spend_on_chair() -> None:
    router = FakeRouter([_response("reject", 95), _response("approve", 92)])

    result = await run_engineering_council({}, {}, _settings(), router)

    assert result["decision"] == "manual_review"
    assert result["route"] == "standard-rejection"
    assert len(router.calls) == 2


@pytest.mark.asyncio
async def test_ambiguous_reviews_do_not_call_unapproved_expert() -> None:
    router = FakeRouter([_response("approve", 70), _response("manual_review", 75)])

    result = await run_engineering_council({}, {}, _settings(), router)

    assert result["decision"] == "manual_review"
    assert result["route"] == "expert-not-enabled"
    assert len(router.calls) == 2


@pytest.mark.asyncio
async def test_approved_expert_adjudicates_ambiguous_reviews() -> None:
    router = FakeRouter(
        [
            _response("approve", 82),
            _response("manual_review", 80),
            _response("approve", 94, "expert approved"),
        ]
    )
    settings = _settings(
        rms_engineering_council_expert_enabled=True,
        rms_engineering_council_expert_justification_id="approval-42",
        rms_model_governance_premium_approvals={
            "RMS_ENGINEERING_COUNCIL_CHAIR_MODEL": "approval-42"
        },
        rms_model_governance_premium_approval_expiries={
            "RMS_ENGINEERING_COUNCIL_CHAIR_MODEL": (
                datetime.now(UTC) + timedelta(days=1)
            ).isoformat()
        },
    )

    result = await run_engineering_council({}, {}, settings, router)

    assert result["decision"] == "approve_micro_surgery"
    assert result["route"] == "expert-adjudication"
    assert result["premiumJustificationId"] == "approval-42"
    assert len(router.calls) == 3
    assert router.calls[-1]["justification_id"] == "approval-42"


@pytest.mark.asyncio
async def test_nonpremium_chair_can_adjudicate_without_premium_approval() -> None:
    router = FakeRouter(
        [
            _response("approve", 82),
            _response("manual_review", 80),
            _response("approve", 90),
        ]
    )
    settings = _settings(rms_engineering_council_chair_model="provider/standard-chair")

    result = await run_engineering_council({}, {}, settings, router)

    assert result["decision"] == "approve_micro_surgery"
    assert result["route"] == "chair-adjudication"
    assert result["premiumJustificationId"] is None
    assert len(router.calls) == 3


@pytest.mark.asyncio
async def test_near_threshold_standard_approval_is_accepted() -> None:
    router = FakeRouter([_response("approve", 81), _response("approve", 80)])

    result = await run_engineering_council({}, {}, _settings(), router)

    assert result["decision"] == "approve_micro_surgery"
    assert result["acceptedUnderTolerance"] is True
    assert result["threshold"] == 85
    assert result["effectiveThreshold"] == 80
    assert len(router.calls) == 2
