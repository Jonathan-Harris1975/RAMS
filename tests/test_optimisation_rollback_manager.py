"""Tests for repo_mgmt.optimisation.rollback_manager."""

from __future__ import annotations

import pytest

from repo_mgmt.optimisation.rollback_manager import RollbackError, RollbackManager


def test_snapshot_and_restore_round_trip(tmp_path) -> None:
    manager = RollbackManager(tmp_path / "rollback")
    state = {"value": "before"}

    manager.snapshot(action_id="act-1", target="scheduler.interval", before=dict(state))
    state["value"] = "after"

    def apply_fn(target_state):
        state.update(target_state)

    restored = manager.restore("act-1", apply_fn=apply_fn)
    assert state["value"] == "before"
    assert restored.target == "scheduler.interval"


def test_restore_without_snapshot_raises(tmp_path) -> None:
    manager = RollbackManager(tmp_path / "rollback")
    with pytest.raises(RollbackError):
        manager.restore("does-not-exist", apply_fn=lambda s: None)


def test_verify_and_maybe_rollback_success_leaves_state_unchanged(tmp_path) -> None:
    manager = RollbackManager(tmp_path / "rollback")
    state = {"value": "before"}
    manager.snapshot(action_id="act-2", target="prompts.system", before=dict(state))
    state["value"] = "after"

    def apply_fn(target_state):
        state.update(target_state)

    verified, snap = manager.verify_and_maybe_rollback(
        action_id="act-2", verify_fn=lambda: True, apply_fn=apply_fn
    )
    assert verified is True
    assert snap is None
    assert state["value"] == "after"


def test_verify_and_maybe_rollback_failure_restores_state(tmp_path) -> None:
    manager = RollbackManager(tmp_path / "rollback")
    state = {"value": "before"}
    manager.snapshot(action_id="act-3", target="prompts.system", before=dict(state))
    state["value"] = "after"

    def apply_fn(target_state):
        state.update(target_state)

    verified, snap = manager.verify_and_maybe_rollback(
        action_id="act-3", verify_fn=lambda: False, apply_fn=apply_fn
    )
    assert verified is False
    assert snap is not None
    assert state["value"] == "before"


def test_verify_fn_exception_triggers_rollback(tmp_path) -> None:
    manager = RollbackManager(tmp_path / "rollback")
    state = {"value": "before"}
    manager.snapshot(action_id="act-4", target="prompts.system", before=dict(state))
    state["value"] = "after"

    def apply_fn(target_state):
        state.update(target_state)

    def broken_verify():
        raise RuntimeError("verification blew up")

    verified, snap = manager.verify_and_maybe_rollback(
        action_id="act-4", verify_fn=broken_verify, apply_fn=apply_fn
    )
    assert verified is False
    assert state["value"] == "before"


def test_prune_keeps_only_configured_snapshot_count(tmp_path) -> None:
    manager = RollbackManager(tmp_path / "rollback", keep_snapshots=2)
    for i in range(5):
        manager.snapshot(action_id=f"act-{i}", target="x", before={"n": i})
    remaining = list((tmp_path / "rollback").glob("*.json"))
    assert len(remaining) <= 2
