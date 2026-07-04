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

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from repo_mgmt.optimisation.history import OptimisationHistoryStore
from repo_mgmt.optimisation.models import OptimisationAction, OptimisationOutcome, new_id
from repo_mgmt.optimisation.rollback_manager import RollbackManager

logger = logging.getLogger(__name__)


@dataclass
class ExperimentRecord:
    """Full before/after lifecycle of one applied optimisation action."""

    experiment_id: str
    action_id: str
    signature: str
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
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "experiment",
            "experiment_id": self.experiment_id,
            "action_id": self.action_id,
            "signature": self.signature,
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
            "detail": self.detail,
        }


class ExperimentManager:
    """Runs and records before/after experiments for auto-configure actions.

    Two independent oscillation-protection guards run before any experiment
    is applied, both keyed on ``action.signature`` (the same
    ``pipeline:category:signal`` grouping key trend analysis uses):

    * ``cooldown_hours`` -- refuse to run a new auto-configure experiment
      for a signature until at least this long has passed since the last
      *finished* (verified or rolled_back) experiment for that same
      signature. ``0`` (the default) disables this guard.
    * ``reversal_lookback`` -- after the cooldown guard passes, look at the
      last N finished experiments for the signature; if their ``after``
      states have been flipping back and forth between the same two values,
      refuse to run and flag for manual review instead. ``0`` or ``1``
      disables this guard (there is nothing to compare).

    A blocked run never calls ``apply_fn``/``verify_fn`` and never creates a
    rollback snapshot -- it records a single ``rejected`` experiment entry
    explaining why, so the block itself is visible in the audit trail.
    """

    def __init__(
        self,
        history: OptimisationHistoryStore,
        rollback: RollbackManager,
        *,
        cooldown_hours: float = 0.0,
        reversal_lookback: int = 0,
    ) -> None:
        self._history = history
        self._rollback = rollback
        self._cooldown_hours = max(0.0, cooldown_hours)
        self._reversal_lookback = max(0, reversal_lookback)

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

        If the cooldown or oscillation guard blocks this signature, the
        change is never applied and the returned record has outcome
        ``rejected`` with ``detail`` explaining why.
        """
        started = datetime.now(timezone.utc)
        blocked_reason = self._blocked_reason(action)
        if blocked_reason is not None:
            record = ExperimentRecord(
                experiment_id=new_id("exp"),
                action_id=action.action_id,
                signature=action.signature,
                pipeline=action.pipeline,
                category=action.category,
                before=before,
                after=after,
                audit_ids=tuple(action.supporting_audit_ids),
                confidence_score=action.confidence_score,
                started_at=started.isoformat(),
                finished_at=started.isoformat(),
                duration_seconds=0.0,
                outcome="rejected",
                detail=blocked_reason,
            )
            self._history.append(action.pipeline, record.to_dict())
            logger.info(
                "experiment_manager: blocked auto_configure for signature %s: %s",
                action.signature,
                blocked_reason,
            )
            return record

        record = ExperimentRecord(
            experiment_id=new_id("exp"),
            action_id=action.action_id,
            signature=action.signature,
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

    def _finished_experiments(self, action: OptimisationAction) -> list[dict[str, Any]]:
        """Finished (verified/rolled_back) experiment records for a signature, oldest first."""
        records = self._history.query(
            action.pipeline, record_type="experiment", signature=action.signature
        )
        finished = [
            record
            for record in records
            if record.get("outcome") in ("verified", "rolled_back") and record.get("finished_at")
        ]
        finished.sort(key=lambda record: str(record.get("finished_at") or ""))
        return finished

    def _blocked_reason(self, action: OptimisationAction) -> str | None:
        """Return a human-readable block reason, or None if the run may proceed."""
        if self._cooldown_hours <= 0 and self._reversal_lookback <= 1:
            return None

        finished = self._finished_experiments(action)
        if not finished:
            return None

        if self._cooldown_hours > 0:
            last_finished = _parse_iso(finished[-1].get("finished_at"))
            if last_finished is not None:
                elapsed_hours = (
                    datetime.now(timezone.utc) - last_finished
                ).total_seconds() / 3600.0
                if elapsed_hours < self._cooldown_hours:
                    return (
                        f"cooldown active: last auto_configure experiment for signature "
                        f"{action.signature} finished {elapsed_hours:.1f}h ago "
                        f"(< {self._cooldown_hours:.1f}h minimum re-optimisation interval)"
                    )

        if self._reversal_lookback > 1 and len(finished) >= self._reversal_lookback:
            recent = finished[-self._reversal_lookback :]
            after_values = [
                json.dumps(record.get("after"), sort_keys=True, default=str) for record in recent
            ]
            distinct_values = set(after_values)
            flips = sum(
                1 for i in range(1, len(after_values)) if after_values[i] != after_values[i - 1]
            )
            # Two (or fewer) distinct "after" states flipping on almost every
            # step is the oscillation signature: A -> B -> A -> B rather than
            # steady convergence toward a single value.
            if len(distinct_values) <= 2 and flips >= self._reversal_lookback - 1:
                return (
                    f"oscillation guard: last {len(recent)} auto_configure experiments for "
                    f"signature {action.signature} have been flipping between "
                    f"{len(distinct_values)} value(s); manual review required before "
                    "further automatic changes"
                )

        return None


def _parse_iso(value: Any) -> datetime | None:
    """Best-effort ISO-8601 parse; returns None rather than raising on bad input."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None
