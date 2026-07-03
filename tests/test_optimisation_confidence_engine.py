"""Tests for repo_mgmt.optimisation.confidence_engine."""

from __future__ import annotations

from repo_mgmt.optimisation.confidence_engine import ConfidenceEngine
from repo_mgmt.optimisation.models import AuditEvidence
from repo_mgmt.optimisation.policy import load_policy


def _evidence(**overrides) -> AuditEvidence:
    defaults = dict(
        audit_id="audit-1",
        pipeline="mobile-ux",
        category="prompts",
        signal="drift",
        observed_value=9.0,
        expected_value=3.0,
        severity="high",
        sample_size=5,
    )
    defaults.update(overrides)
    return AuditEvidence(**defaults)


def test_single_cycle_is_capped_at_single_anomaly_ceiling() -> None:
    policy = load_policy()
    engine = ConfidenceEngine(policy)
    result = engine.score(evidence=[_evidence()], distinct_cycles=1, category="prompts")
    assert result.score <= policy.trend_analysis.single_anomaly_max_confidence
    assert result.tier == "observe"


def test_strong_recurring_evidence_reaches_high_confidence() -> None:
    policy = load_policy()
    engine = ConfidenceEngine(policy)
    evidence = [
        _evidence(audit_id=f"audit-{i}", sample_size=8, severity="critical") for i in range(6)
    ]
    result = engine.score(evidence=evidence, distinct_cycles=6, category="prompts")
    assert result.score > 90
    assert result.tier == "patch_candidate"


def test_weak_evidence_scores_low() -> None:
    policy = load_policy()
    engine = ConfidenceEngine(policy)
    evidence = [_evidence(observed_value=3.1, expected_value=3.0, severity="low", sample_size=1)]
    result = engine.score(evidence=evidence, distinct_cycles=2, category="prompts")
    assert result.score < 70


def test_effective_tier_respects_category_ceiling() -> None:
    policy = load_policy()
    engine = ConfidenceEngine(policy)
    evidence = [
        _evidence(audit_id=f"audit-{i}", category="scheduler", sample_size=8, severity="critical")
        for i in range(6)
    ]
    result = engine.score(evidence=evidence, distinct_cycles=6, category="scheduler")
    assert result.tier == "patch_candidate"
    # scheduler's policy ceiling is "recommend"
    assert result.effective_tier == "recommend"


def test_components_are_explainable_and_bounded() -> None:
    policy = load_policy()
    engine = ConfidenceEngine(policy)
    result = engine.score(evidence=[_evidence()], distinct_cycles=3, category="prompts")
    for key in ("evidence_strength", "sample_size", "recurrence", "severity"):
        assert 0.0 <= result.components[key] <= 1.0
    assert "score=" in result.rationale
