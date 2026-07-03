"""Tests for repo_mgmt.optimisation.experiment_manager."""

from __future__ import annotations

from repo_mgmt.optimisation.experiment_manager import ExperimentManager
from repo_mgmt.optimisation.history import OptimisationHistoryStore
from repo_mgmt.optimisation.models import OptimisationAction
from repo_mgmt.optimisation.rollback_manager import RollbackManager


def _action(**overrides) -> OptimisationAction:
    defaults = dict(
        action_id="act-1",
        signature="sig-1",
        pipeline="mobile-ux",
        category="prompts",
        signal="drift",
        description="prompt drift detected",
        supporting_audit_ids=["audit-1", "audit-2", "audit-3"],
        supporting_cycles=3,
        confidence_score=93.5,
        tier="auto_configure",
    )
    defaults.update(overrides)
    return OptimisationAction(**defaults)


def _managers(tmp_path):
    history = OptimisationHistoryStore(tmp_path / "history")
    rollback = RollbackManager(tmp_path / "rollback")
    return ExperimentManager(history, rollback), history


def test_verified_experiment_records_full_lifecycle(tmp_path) -> None:
    manager, history = _managers(tmp_path)
    action = _action()
    state = {"value": "old"}

    def apply_fn(target):
        state.update(target)

    record = manager.run(
        action=action,
        before={"value": "old"},
        after={"value": "new"},
        apply_fn=apply_fn,
        verify_fn=lambda: True,
    )

    assert record.outcome == "verified"
    assert record.before == {"value": "old"}
    assert record.after == {"value": "new"}
    assert record.audit_ids == ("audit-1", "audit-2", "audit-3")
    assert record.confidence_score == 93.5
    assert record.duration_seconds is not None
    assert record.finished_at is not None
    assert state["value"] == "new"

    stored = manager.history_for("mobile-ux")
    outcomes = [r["outcome"] for r in stored]
    assert "pending" in outcomes
    assert "verified" in outcomes


def test_failed_verification_rolls_back_and_records_outcome(tmp_path) -> None:
    manager, _ = _managers(tmp_path)
    action = _action(action_id="act-2")
    state = {"value": "old"}

    def apply_fn(target):
        state.update(target)

    record = manager.run(
        action=action,
        before={"value": "old"},
        after={"value": "new"},
        apply_fn=apply_fn,
        verify_fn=lambda: False,
    )

    assert record.outcome == "rolled_back"
    assert state["value"] == "old"


def test_audit_ids_are_deduplicated_and_sorted() -> None:
    action = _action(supporting_audit_ids=["audit-3", "audit-1", "audit-1", "audit-2"])
    assert action.supporting_audit_ids == ["audit-1", "audit-2", "audit-3"]
