"""
Experiment Manager for the RAMS Optimisation Subsystem.

Wraps every applied optimisation action in an experiment record capturing
exactly what was asked for: before state, after state, the audit ids that
justified the change, the confidence score, how long verification took, and
the final outcome. Records are written to the Optimisation History as they
progress (started -> verified/rolled_back), so the full lifecycle is
reconstructable from the audit log alone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from repo_mgmt.optimisation.history import OptimisationHistoryStore
from repo_mgmt.optimisation.models import OptimisationAction, OptimisationOutcome, new_id
from repo_mgmt.optimisation.rollback_manager import RollbackManager


@dataclass
class ExperimentRecord:
    """Full before/after lifecycle of one applied optimisation action."""

    experiment_id: str
    action_id: str
    pipeline: str
    category: str
    before: dict[str, Any]
    after: dict[str, Any]
    audit_ids: tuple[str, ...]
    confidence_score: float
    started_at: str
    finished_at: str | None = None
    duration_seconds: float | None = None
    outcome: OptimisationOutcome = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "experiment",
            "experiment_id": self.experiment_id,
            "action_id": self.action_id,
            "pipeline": self.pipeline,
            "category": self.category,
            "before": self.before,
            "after": self.after,
            "audit_ids": list(self.audit_ids),
            "confidence_score": self.confidence_score,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "outcome": self.outcome,
        }


class ExperimentManager:
    """Runs and records before/after experiments for auto-configure actions."""

    def __init__(self, history: OptimisationHistoryStore, rollback: RollbackManager) -> None:
        self._history = history
        self._rollback = rollback

    def run(
        self,
        *,
        action: OptimisationAction,
        before: dict[str, Any],
        after: dict[str, Any],
        apply_fn: Callable[[dict[str, Any]], None],
        verify_fn: Callable[[], bool],
    ) -> ExperimentRecord:
        """Apply, time, verify, and durably record one optimisation action.

        On verification failure the change is rolled back automatically via
        the Rollback Manager and the experiment is recorded with outcome
        ``rolled_back``; the caller does not need to handle rollback itself.
        """
        started = datetime.now(timezone.utc)
        record = ExperimentRecord(
            experiment_id=new_id("exp"),
            action_id=action.action_id,
            pipeline=action.pipeline,
            category=action.category,
            before=before,
            after=after,
            audit_ids=tuple(action.supporting_audit_ids),
            confidence_score=action.confidence_score,
            started_at=started.isoformat(),
        )
        self._history.append(action.pipeline, {**record.to_dict(), "outcome": "pending"})

        self._rollback.snapshot(action_id=action.action_id, target=action.signal, before=before)

        t0 = time.monotonic()
        apply_fn(after)
        verified, rolled_back_snapshot = self._rollback.verify_and_maybe_rollback(
            action_id=action.action_id,
            verify_fn=verify_fn,
            apply_fn=apply_fn,
        )
        duration = time.monotonic() - t0
        finished = datetime.now(timezone.utc)

        record.finished_at = finished.isoformat()
        record.duration_seconds = round(duration, 3)
        record.outcome = "verified" if verified else "rolled_back"

        self._history.append(action.pipeline, record.to_dict())
        return record

    def history_for(self, pipeline: str) -> list[dict[str, Any]]:
        """Return every experiment record (all lifecycle stages) for a pipeline."""
        return self._history.query(pipeline, record_type="experiment")
