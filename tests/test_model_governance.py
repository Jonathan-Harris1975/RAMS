"""Tests for cost-aware, retirement-safe RAMS model governance."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from repo_mgmt.model_governance import (
    apply_rams_model_governance,
    build_rams_model_assignments,
    restore_rams_model_governance,
)


def _future(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _approval(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "justification_id": "premium-approval-1",
        "owner": "RAMS service owner",
        "reason": "The cheaper candidate missed confirmed high-risk defects.",
        "cheaper_model_tested": "openai/gpt-5.6-sol",
        "evidence": ["eval/rams-council-2026-09.json"],
        "approved_by": "HIVE AI Council",
        "expires_at": _future(31),
    }
    data.update(overrides)
    return data


def test_evaluated_candidates_are_selected_by_cost_before_score() -> None:
    registry = {
        "coding": [
            {
                "model_id": "provider/high-score",
                "score": 0.99,
                "evaluation_passed": True,
                "estimated_cost_per_task": 0.09,
            },
            {
                "model_id": "provider/qualified-low-cost",
                "score": 0.94,
                "evaluation_passed": True,
                "estimated_cost_per_task": 0.01,
            },
        ]
    }

    assignments = build_rams_model_assignments(registry)

    assert assignments["OPENROUTER_PRIMARY_MODEL"] == "provider/qualified-low-cost"
    assert assignments["OPENROUTER_SECONDARY_MODEL"] == "provider/high-score"


def test_legacy_score_only_registry_remains_compatible() -> None:
    registry = {
        "coding": [
            {"model_id": "provider/lower", "score": 0.8},
            {"model_id": "provider/higher", "score": 0.9},
        ],
        "fast": [{"model_id": "provider/triage", "score": 1.0}],
    }

    assignments = build_rams_model_assignments(registry)

    assert assignments["OPENROUTER_PRIMARY_MODEL"] == "provider/higher"
    assert assignments["OPENROUTER_SECONDARY_MODEL"] == "provider/lower"
    assert assignments["OPENROUTER_TRIAGE_MODEL"] == "provider/triage"


@pytest.mark.parametrize(
    ("metadata", "excluded"),
    [
        ({"status": "retired"}, True),
        ({"available": False}, True),
        ({"privacy_compatible": False}, True),
        ({"evaluation_passed": False}, True),
        ({"expiration_date": "not-a-date"}, True),
        ({"expiration_date": _future(10)}, True),
        ({"expiration_date": _future(60)}, False),
    ],
)
def test_ineligible_and_near_retirement_models_are_excluded(
    metadata: dict[str, object], excluded: bool
) -> None:
    registry = {
        "coding": [
            {"model_id": "provider/candidate", "score": 1.0, **metadata},
            {"model_id": "provider/fallback", "score": 0.5},
        ]
    }

    assignments = build_rams_model_assignments(registry)

    expected = "provider/fallback" if excluded else "provider/candidate"
    assert assignments["OPENROUTER_PRIMARY_MODEL"] == expected


def test_known_deprecated_openrouter_alias_is_excluded() -> None:
    registry = {
        "coding": [
            {"model_id": "anthropic/claude-opus-5-fast", "score": 1.0},
            {"model_id": "openai/gpt-5.6-sol", "score": 0.9},
        ]
    }

    assignments = build_rams_model_assignments(registry)

    assert assignments["OPENROUTER_PRIMARY_MODEL"] == "openai/gpt-5.6-sol"


def test_structured_output_requirement_filters_code_models() -> None:
    registry = {
        "coding": [
            {
                "model_id": "provider/plain-text",
                "score": 1.0,
                "supported_parameters": ["temperature", "max_tokens"],
            },
            {
                "model_id": "provider/json",
                "score": 0.9,
                "supported_parameters": ["response_format", "max_tokens"],
            },
        ]
    }

    assignments = build_rams_model_assignments(registry)

    assert assignments["OPENROUTER_PRIMARY_MODEL"] == "provider/json"


def test_premium_selection_without_justification_is_rejected(settings, mock_r2) -> None:
    registry = {
        "expert": [
            {
                "model_id": "anthropic/claude-opus-5",
                "score": 1.0,
                "approved_roles": ["chair"],
            }
        ],
        "coding": [{"model_id": "openai/gpt-5.6-luna", "score": 1.0}],
    }

    with pytest.raises(TypeError, match="structured premium_justification"):
        apply_rams_model_governance(settings, mock_r2, registry=registry, source_run_id="council-1")

    mock_r2.put_object.assert_not_called()


def test_complete_premium_justification_is_persisted_and_activated(settings, mock_r2) -> None:
    registry = {
        "coding": [
            {
                "model_id": "openai/gpt-5.6-luna",
                "score": 1.0,
                "approved_roles": ["primary", "secondary", "specialist"],
            }
        ],
        "reasoning": [
            {
                "model_id": "openai/gpt-5.6-sol",
                "score": 1.0,
                "approved_roles": ["architect"],
            }
        ],
        "fast": [
            {
                "model_id": "google/gemini-2.5-flash-lite",
                "score": 1.0,
                "approved_roles": ["triage"],
            }
        ],
        "expert": [
            {
                "model_id": "anthropic/claude-opus-5",
                "score": 1.0,
                "approved_roles": ["chair"],
                "premium_justification": _approval(),
            }
        ],
    }

    result = apply_rams_model_governance(
        settings, mock_r2, registry=registry, source_run_id="council-2"
    )

    assert result["schemaVersion"] == "rams-model-governance/v2"
    assert result["policy"]["budgetBehaviour"] == ("observe-and-review-never-stop-authorised-work")
    assert result["decisions"]["RMS_ENGINEERING_COUNCIL_CHAIR_MODEL"]["premium"] is True
    assert settings.rms_engineering_council_expert_enabled is True
    assert settings.rms_engineering_council_expert_justification_id == ("premium-approval-1")
    assert settings.rms_model_governance_premium_roles == {"RMS_ENGINEERING_COUNCIL_CHAIR_MODEL"}
    assert settings.rms_model_governance_premium_models == {"anthropic/claude-opus-5"}
    persisted = json.loads(mock_r2.put_object.call_args.args[2])
    assert persisted["premiumApprovals"]["RMS_ENGINEERING_COUNCIL_CHAIR_MODEL"]["expiresAt"]


def test_expired_premium_justification_is_rejected(settings, mock_r2) -> None:
    registry = {
        "expert": [
            {
                "model_id": "anthropic/claude-opus-5",
                "approved_roles": ["chair"],
                "premium_justification": _approval(expires_at=_future(-1)),
            }
        ]
    }

    with pytest.raises(ValueError, match="has expired"):
        apply_rams_model_governance(
            settings, mock_r2, registry=registry, source_run_id="council-expired"
        )


def test_restore_drops_expired_premium_approval(settings, mock_r2) -> None:
    mock_r2.object_exists.return_value = True
    mock_r2.get_object.return_value = json.dumps(
        {
            "schemaVersion": "rams-model-governance/v2",
            "sourceRunId": "old-council",
            "assignments": {"RMS_ENGINEERING_COUNCIL_CHAIR_MODEL": "anthropic/claude-opus-5"},
            "decisions": {
                "RMS_ENGINEERING_COUNCIL_CHAIR_MODEL": {
                    "modelId": "anthropic/claude-opus-5",
                    "premium": True,
                }
            },
            "premiumApprovals": {
                "RMS_ENGINEERING_COUNCIL_CHAIR_MODEL": {
                    "justificationId": "expired-approval",
                    "expiresAt": _future(-1),
                }
            },
        }
    ).encode()
    settings.rms_engineering_council_expert_enabled = True

    result = restore_rams_model_governance(settings, mock_r2)

    assert result["restored"] is True
    assert settings.rms_engineering_council_expert_enabled is False
    assert settings.rms_engineering_council_expert_justification_id == ""
    assert settings.rms_model_governance_premium_roles == {"RMS_ENGINEERING_COUNCIL_CHAIR_MODEL"}


def test_premium_approval_cannot_exceed_ninety_days(settings, mock_r2) -> None:
    registry = {
        "expert": [
            {
                "model_id": "anthropic/claude-opus-5",
                "approved_roles": ["chair"],
                "premium_justification": _approval(expires_at=_future(91)),
            }
        ]
    }

    with pytest.raises(ValueError, match="exceeds 90 days"):
        apply_rams_model_governance(
            settings, mock_r2, registry=registry, source_run_id="council-too-long"
        )
