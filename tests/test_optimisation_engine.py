"""Integration tests for repo_mgmt.optimisation.optimisation_engine."""

from __future__ import annotations

from repo_mgmt.optimisation.confidence_engine import ConfidenceEngine
from repo_mgmt.optimisation.experiment_manager import ExperimentManager
from repo_mgmt.optimisation.history import OptimisationHistoryStore
from repo_mgmt.optimisation.models import AuditEvidence
from repo_mgmt.optimisation.optimisation_engine import OptimisationEngine
from repo_mgmt.optimisation.policy import load_policy
from repo_mgmt.optimisation.rollback_manager import RollbackManager
from repo_mgmt.optimisation.trend_analysis import TrendAnalyser


def _build_engine(tmp_path) -> OptimisationEngine:
    policy = load_policy()
    history = OptimisationHistoryStore(tmp_path / "history")
    trend = TrendAnalyser(policy, history)
    confidence = ConfidenceEngine(policy)
    rollback = RollbackManager(tmp_path / "rollback", keep_snapshots=policy.rollback.keep_snapshots)
    experiments = ExperimentManager(history, rollback)
    return OptimisationEngine(policy, history, trend, confidence, experiments)


def _evidence(audit_id: str, **overrides) -> AuditEvidence:
    defaults = dict(
        audit_id=audit_id,
        pipeline="seo-aeo-geo",
        category="scheduler",
        signal="retry_backoff_too_aggressive",
        observed_value=9.0,
        expected_value=3.0,
        severity="high",
        sample_size=5,
    )
    defaults.update(overrides)
    return AuditEvidence(**defaults)


def test_single_anomaly_never_produces_an_action(tmp_path) -> None:
    engine = _build_engine(tmp_path)
    ev = _evidence("audit-1")
    engine.ingest_findings([ev])
    action = engine.evaluate("seo-aeo-geo", ev.signature)
    assert action is None


def test_repeated_evidence_produces_scored_action(tmp_path) -> None:
    engine = _build_engine(tmp_path)
    evs = [_evidence(f"audit-{i}") for i in range(3)]
    engine.ingest_findings(evs)
    action = engine.evaluate("seo-aeo-geo", evs[0].signature)
    assert action is not None
    assert action.supporting_cycles == 3
    assert set(action.supporting_audit_ids) == {"audit-0", "audit-1", "audit-2"}
    assert 0.0 <= action.confidence_score <= 100.0


def test_scheduler_category_never_auto_applies_regardless_of_confidence(tmp_path) -> None:
    """Even overwhelming evidence for 'scheduler' must stay at recommend or below."""
    engine = _build_engine(tmp_path)
    evs = [
        _evidence(f"audit-{i}", sample_size=10, severity="critical") for i in range(8)
    ]
    engine.ingest_findings(evs)
    action = engine.evaluate("seo-aeo-geo", evs[0].signature)
    assert action is not None
    assert action.tier in ("observe", "recommend")


def test_auto_configure_action_applies_and_verifies(tmp_path) -> None:
    engine = _build_engine(tmp_path)
    evs = [
        AuditEvidence(
            audit_id=f"audit-p{i}",
            pipeline="mobile-ux",
            category="prompts",
            signal="system_prompt_drift",
            observed_value=0.9,
            expected_value=0.2,
            severity="critical",
            sample_size=8,
        )
        for i in range(4)
    ]
    engine.ingest_findings(evs)
    action = engine.evaluate("mobile-ux", evs[0].signature)
    assert action is not None
    assert action.tier == "auto_configure"

    state = {"value": "old"}

    def apply_fn(target):
        state.update(target)

    result = engine.route(
        action,
        before={"value": "old"},
        after={"value": "new"},
        apply_fn=apply_fn,
        verify_fn=lambda: True,
    )
    assert result.routed_to == "applied"
    assert state["value"] == "new"
    assert result.experiment is not None
    assert result.experiment.outcome == "verified"


def test_auto_configure_action_rolls_back_on_failed_verification(tmp_path) -> None:
    engine = _build_engine(tmp_path)
    evs = [
        AuditEvidence(
            audit_id=f"audit-p{i}",
            pipeline="mobile-ux",
            category="prompts",
            signal="system_prompt_drift",
            observed_value=0.9,
            expected_value=0.2,
            severity="critical",
            sample_size=8,
        )
        for i in range(4)
    ]
    engine.ingest_findings(evs)
    action = engine.evaluate("mobile-ux", evs[0].signature)
    assert action is not None

    state = {"value": "old"}

    def apply_fn(target):
        state.update(target)

    result = engine.route(
        action,
        before={"value": "old"},
        after={"value": "new"},
        apply_fn=apply_fn,
        verify_fn=lambda: False,
    )
    assert result.routed_to == "rolled_back"
    assert state["value"] == "old"


def test_patch_candidate_tier_is_never_reached_by_default_policy(tmp_path) -> None:
    """No category ships enabled for patch_candidate by default (fail-closed);
    even overwhelming evidence for a category capped at auto_configure stays
    there rather than escalating to code-level patch generation."""
    engine = _build_engine(tmp_path)
    evs = [
        AuditEvidence(
            audit_id=f"audit-p{i}",
            pipeline="mobile-ux",
            category="podcasts",
            signal="transcript_alignment_drift",
            observed_value=0.95,
            expected_value=0.1,
            severity="critical",
            sample_size=10,
        )
        for i in range(6)
    ]
    engine.ingest_findings(evs)
    action = engine.evaluate("mobile-ux", evs[0].signature)
    assert action is not None
    assert action.tier != "patch_candidate"


def test_patch_candidate_tier_routing_never_applies_anything(tmp_path) -> None:
    """Directly exercise the patch_candidate branch of route(): it must only
    record a 'pending' marker and hand off to the Patch Generator, never
    calling any apply/verify function itself."""
    from repo_mgmt.optimisation.models import OptimisationAction

    engine = _build_engine(tmp_path)
    action = OptimisationAction(
        action_id="act-patch-1",
        signature="sig-patch-1",
        pipeline="mobile-ux",
        category="podcasts",
        signal="transcript_alignment_drift",
        description="manually constructed patch-candidate action for routing test",
        supporting_audit_ids=["audit-1", "audit-2", "audit-3"],
        supporting_cycles=6,
        confidence_score=99.0,
        tier="patch_candidate",
    )
    result = engine.route(action)  # deliberately no apply_fn/verify_fn
    assert result.routed_to == "patch_pending"
    assert result.experiment is None


def test_evaluate_is_idempotent_for_same_signature(tmp_path) -> None:
    engine = _build_engine(tmp_path)
    evs = [_evidence(f"audit-{i}") for i in range(3)]
    engine.ingest_findings(evs)
    action1 = engine.evaluate("seo-aeo-geo", evs[0].signature)
    action2 = engine.evaluate("seo-aeo-geo", evs[0].signature)
    assert action1.action_id == action2.action_id
