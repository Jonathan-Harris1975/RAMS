"""Tests for repo_mgmt.optimisation.experiment_manager."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

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


def test_experiment_record_carries_signature(tmp_path) -> None:
    manager, history = _managers(tmp_path)
    action = _action(signature="sig-carries-through")
    manager.run(
        action=action,
        before={"value": "old"},
        after={"value": "new"},
        apply_fn=lambda target: None,
        verify_fn=lambda: True,
    )
    records = manager.history_for("mobile-ux")
    assert all(r["signature"] == "sig-carries-through" for r in records)


class TestCooldownGuard:
    def _managers(self, tmp_path, **kwargs):
        history = OptimisationHistoryStore(tmp_path / "history")
        rollback = RollbackManager(tmp_path / "rollback")
        return ExperimentManager(history, rollback, **kwargs), history

    def test_second_run_within_cooldown_is_rejected_without_applying(self, tmp_path) -> None:
        manager, _ = self._managers(tmp_path, cooldown_hours=24.0)
        action = _action()
        state = {"value": "old"}

        def apply_fn(target):
            state.update(target)

        first = manager.run(
            action=action,
            before={"value": "old"},
            after={"value": "new"},
            apply_fn=apply_fn,
            verify_fn=lambda: True,
        )
        assert first.outcome == "verified"

        second = manager.run(
            action=action,
            before={"value": "new"},
            after={"value": "newer"},
            apply_fn=apply_fn,
            verify_fn=lambda: True,
        )

        assert second.outcome == "rejected"
        assert "cooldown" in second.detail
        # apply_fn was never called for the rejected run, so state is unchanged.
        assert state["value"] == "new"

    def test_run_after_cooldown_window_is_allowed(self, tmp_path) -> None:
        manager, history = self._managers(tmp_path, cooldown_hours=1.0)
        action = _action()
        state = {"value": "old"}

        def apply_fn(target):
            state.update(target)

        manager.run(
            action=action,
            before={"value": "old"},
            after={"value": "new"},
            apply_fn=apply_fn,
            verify_fn=lambda: True,
        )

        # Backdate the finished experiment record so it looks old enough.
        records = list(history.read_all(action.pipeline))
        for record in records:
            if record.get("type") == "experiment" and record.get("outcome") == "verified":
                record["finished_at"] = (
                    datetime.now(timezone.utc) - timedelta(hours=2)
                ).isoformat()
        path = history._file_for(action.pipeline)
        path.write_text(
            "\n".join(json.dumps(r, default=str, sort_keys=True) for r in records) + "\n",
            encoding="utf-8",
        )

        second = manager.run(
            action=action,
            before={"value": "new"},
            after={"value": "newer"},
            apply_fn=apply_fn,
            verify_fn=lambda: True,
        )

        assert second.outcome == "verified"
        assert state["value"] == "newer"

    def test_cooldown_disabled_by_default(self, tmp_path) -> None:
        manager, _ = self._managers(tmp_path)
        action = _action()
        state = {"value": "old"}

        def apply_fn(target):
            state.update(target)

        manager.run(
            action=action,
            before={"value": "old"},
            after={"value": "new"},
            apply_fn=apply_fn,
            verify_fn=lambda: True,
        )
        second = manager.run(
            action=action,
            before={"value": "new"},
            after={"value": "newer"},
            apply_fn=apply_fn,
            verify_fn=lambda: True,
        )
        assert second.outcome == "verified"

    def test_cooldown_is_scoped_per_signature(self, tmp_path) -> None:
        manager, _ = self._managers(tmp_path, cooldown_hours=24.0)
        action_a = _action(action_id="act-a", signature="sig-a")
        action_b = _action(action_id="act-b", signature="sig-b")
        state: dict = {}

        def apply_fn(target):
            state.update(target)

        manager.run(
            action=action_a,
            before={},
            after={"value": "a"},
            apply_fn=apply_fn,
            verify_fn=lambda: True,
        )
        second = manager.run(
            action=action_b,
            before={},
            after={"value": "b"},
            apply_fn=apply_fn,
            verify_fn=lambda: True,
        )
        assert second.outcome == "verified"


class TestOscillationGuard:
    def _managers(self, tmp_path, **kwargs):
        history = OptimisationHistoryStore(tmp_path / "history")
        rollback = RollbackManager(tmp_path / "rollback")
        return ExperimentManager(history, rollback, **kwargs), history

    def test_flip_flopping_after_states_trigger_manual_review(self, tmp_path) -> None:
        # cooldown_hours=0 isolates this test to the oscillation guard alone.
        manager, _ = self._managers(tmp_path, cooldown_hours=0.0, reversal_lookback=3)
        action = _action()
        state: dict = {}

        def apply_fn(target):
            state.update(target)

        values = [{"value": "a"}, {"value": "b"}, {"value": "a"}]
        outcomes = []
        for after in values:
            record = manager.run(
                action=action,
                before=dict(state),
                after=after,
                apply_fn=apply_fn,
                verify_fn=lambda: True,
            )
            outcomes.append(record.outcome)

        assert outcomes == ["verified", "verified", "verified"]

        # A fourth run alternating back to "b" should now be blocked: the
        # last 3 finished experiments (a, b, a) flip on every step between
        # only 2 distinct values.
        fourth = manager.run(
            action=action,
            before=dict(state),
            after={"value": "b"},
            apply_fn=apply_fn,
            verify_fn=lambda: True,
        )
        assert fourth.outcome == "rejected"
        assert "oscillation" in fourth.detail

    def test_steady_convergence_is_not_flagged_as_oscillation(self, tmp_path) -> None:
        manager, _ = self._managers(tmp_path, cooldown_hours=0.0, reversal_lookback=3)
        action = _action()
        state: dict = {}

        def apply_fn(target):
            state.update(target)

        for after in [{"value": "a"}, {"value": "b"}, {"value": "c"}, {"value": "d"}]:
            record = manager.run(
                action=action,
                before=dict(state),
                after=after,
                apply_fn=apply_fn,
                verify_fn=lambda: True,
            )
            assert record.outcome == "verified"

    def test_oscillation_guard_disabled_when_lookback_is_zero(self, tmp_path) -> None:
        manager, _ = self._managers(tmp_path, cooldown_hours=0.0, reversal_lookback=0)
        action = _action()
        state: dict = {}

        def apply_fn(target):
            state.update(target)

        for after in [{"value": "a"}, {"value": "b"}, {"value": "a"}, {"value": "b"}]:
            record = manager.run(
                action=action,
                before=dict(state),
                after=after,
                apply_fn=apply_fn,
                verify_fn=lambda: True,
            )
            assert record.outcome == "verified"

