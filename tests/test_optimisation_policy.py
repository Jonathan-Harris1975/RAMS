"""Tests for repo_mgmt.optimisation.policy — externalised thresholds."""

from __future__ import annotations

import json

import pytest

from repo_mgmt.optimisation.policy import (
    OptimisationPolicy,
    PolicyConfigurationError,
    load_policy,
)

VALID_POLICY: dict = {
    "version": 1,
    "confidence_tiers": [
        {"name": "observe", "min": 0, "max": 70},
        {"name": "recommend", "min": 70, "max": 90},
        {"name": "auto_configure", "min": 90, "max": 98},
        {"name": "patch_candidate", "min": 98, "max": 100},
    ],
    "confidence_weights": {
        "evidence_strength": 0.4,
        "sample_size": 0.25,
        "recurrence": 0.25,
        "severity": 0.1,
    },
    "trend_analysis": {
        "min_audit_cycles": 3,
        "min_distinct_evidence_samples": 3,
        "evidence_window_days": 30,
        "single_anomaly_max_confidence": 40,
    },
    "categories": {
        "scheduler": {"enabled": True, "max_tier_without_review": "recommend"},
        "validators": {"enabled": True, "max_tier_without_review": "recommend"},
        "prompts": {"enabled": True, "max_tier_without_review": "auto_configure"},
        "rss": {"enabled": True, "max_tier_without_review": "auto_configure"},
        "podcasts": {"enabled": True, "max_tier_without_review": "auto_configure"},
        "platform_weighting": {"enabled": True, "max_tier_without_review": "auto_configure"},
        "configuration": {"enabled": True, "max_tier_without_review": "recommend"},
    },
    "rollback": {
        "verify_timeout_seconds": 300,
        "auto_rollback_on_verification_failure": True,
        "keep_snapshots": 50,
    },
    "patch_generator": {
        "require_tests": True,
        "require_lint": True,
        "require_regression": True,
        "require_acceptance_criteria": True,
        "require_rollback_package": True,
        "max_files": 8,
        "max_changes": 12,
    },
    "history": {"retention_days": 365},
}


def test_bundled_default_policy_loads() -> None:
    policy = load_policy()
    assert isinstance(policy, OptimisationPolicy)
    assert policy.version == 1


def test_bundled_policy_metadata_keys_are_ignored(tmp_path) -> None:
    doc = {**VALID_POLICY, "$schema": "https://example.com/schema", "description": "docs"}
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(doc))
    policy = load_policy(path)
    assert policy.version == 1


def test_missing_policy_file_raises(tmp_path) -> None:
    with pytest.raises(PolicyConfigurationError):
        load_policy(tmp_path / "does-not-exist.json")


def test_invalid_json_raises(tmp_path) -> None:
    path = tmp_path / "policy.json"
    path.write_text("{not valid json")
    with pytest.raises(PolicyConfigurationError):
        load_policy(path)


def test_gap_in_confidence_tiers_rejected(tmp_path) -> None:
    doc = json.loads(json.dumps(VALID_POLICY))
    doc["confidence_tiers"][1]["min"] = 75  # opens a gap between observe(0-70) and recommend(75-90)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(PolicyConfigurationError):
        load_policy(path)


def test_weights_must_sum_to_one(tmp_path) -> None:
    doc = json.loads(json.dumps(VALID_POLICY))
    doc["confidence_weights"]["severity"] = 0.5  # now sums to 1.4
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(PolicyConfigurationError):
        load_policy(path)


def test_tier_for_score_boundaries(tmp_path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(VALID_POLICY))
    policy = load_policy(path)
    assert policy.tier_for_score(0) == "observe"
    assert policy.tier_for_score(69.99) == "observe"
    assert policy.tier_for_score(70) == "recommend"
    assert policy.tier_for_score(89.99) == "recommend"
    assert policy.tier_for_score(90) == "auto_configure"
    assert policy.tier_for_score(97.99) == "auto_configure"
    assert policy.tier_for_score(98) == "patch_candidate"
    assert policy.tier_for_score(100) == "patch_candidate"


def test_effective_tier_caps_at_category_ceiling(tmp_path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(VALID_POLICY))
    policy = load_policy(path)
    # scheduler is capped at "recommend" even if raw tier is patch_candidate.
    assert policy.effective_tier("scheduler", "patch_candidate") == "recommend"
    assert policy.effective_tier("scheduler", "observe") == "observe"
    # prompts is capped at "auto_configure".
    assert policy.effective_tier("prompts", "patch_candidate") == "auto_configure"
    assert policy.effective_tier("prompts", "recommend") == "recommend"


def test_effective_tier_disabled_category_forces_observe(tmp_path) -> None:
    doc = json.loads(json.dumps(VALID_POLICY))
    doc["categories"]["rss"]["enabled"] = False
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(doc))
    policy = load_policy(path)
    assert policy.effective_tier("rss", "patch_candidate") == "observe"
    assert policy.is_category_enabled("rss") is False
