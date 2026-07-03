"""
Rollback Manager for the RAMS Optimisation Subsystem.

Every applied optimisation action captures a snapshot of the exact
configuration state it changed *before* changing it. If post-change
verification fails (or is never confirmed), the manager restores that
snapshot automatically -- this is what makes ``auto_configure``-tier
actions safe to apply without a human in the loop.

Snapshots are content-addressed and persisted to disk so a restore is
possible even across a process restart (e.g. RAMS is redeployed between an
optimisation being applied and its verification window elapsing).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_DEFAULT_STATE_DIR = Path("data") / "optimisation_rollback"


class RollbackError(Exception):
    """Raised when a snapshot cannot be captured or restored."""


@dataclass(frozen=True)
class ConfigSnapshot:
    """The exact prior state of one configuration target."""

    snapshot_id: str
    action_id: str
    target: str  # opaque identifier for the config surface, e.g. "scheduler.retry_backoff"
    before: dict[str, Any]
    taken_at: str


class RollbackManager:
    """Captures and restores configuration snapshots for optimisation actions."""

    def __init__(self, state_dir: str | Path | None = None, *, keep_snapshots: int = 50) -> None:
        self._state_dir = Path(state_dir) if state_dir else _DEFAULT_STATE_DIR
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._keep_snapshots = keep_snapshots
        self._lock = threading.Lock()

    def _path_for(self, action_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in action_id)
        return self._state_dir / f"{safe}.json"

    def snapshot(self, *, action_id: str, target: str, before: dict[str, Any]) -> ConfigSnapshot:
        """Persist the pre-change state so it can be restored later."""
        snap = ConfigSnapshot(
            snapshot_id=f"snap-{action_id}",
            action_id=action_id,
            target=target,
            before=before,
            taken_at=datetime.now(timezone.utc).isoformat(),
        )
        path = self._path_for(action_id)
        with self._lock:
            path.write_text(
                json.dumps(
                    {
                        "snapshot_id": snap.snapshot_id,
                        "action_id": snap.action_id,
                        "target": snap.target,
                        "before": snap.before,
                        "taken_at": snap.taken_at,
                    },
                    indent=2,
                    sort_keys=True,
                    default=str,
                ),
                encoding="utf-8",
            )
            self._prune_locked()
        return snap

    def _prune_locked(self) -> None:
        """Keep only the most recent ``keep_snapshots`` snapshot files."""
        files = sorted(self._state_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        excess = len(files) - self._keep_snapshots
        for stale in files[:max(0, excess)]:
            stale.unlink(missing_ok=True)

    def load(self, action_id: str) -> ConfigSnapshot | None:
        path = self._path_for(action_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return ConfigSnapshot(**data)

    def restore(
        self,
        action_id: str,
        *,
        apply_fn: Callable[[dict[str, Any]], None],
    ) -> ConfigSnapshot:
        """Restore the pre-change state for ``action_id`` via ``apply_fn``.

        ``apply_fn`` is caller-supplied because the rollback manager itself
        is deliberately agnostic to *what* a configuration target is (a
        scheduler interval, a prompt template id, an RSS/podcast weighting
        table, ...); it only guarantees the exact prior value is handed
        back to be re-applied.
        """
        snap = self.load(action_id)
        if snap is None:
            raise RollbackError(f"no snapshot found for action {action_id!r}; cannot roll back")
        apply_fn(snap.before)
        logger.info("rolled back action %s target=%s", action_id, snap.target)
        return snap

    def verify_and_maybe_rollback(
        self,
        *,
        action_id: str,
        verify_fn: Callable[[], bool],
        apply_fn: Callable[[dict[str, Any]], None],
    ) -> tuple[bool, ConfigSnapshot | None]:
        """Run ``verify_fn``; on failure, restore the snapshot automatically.

        Returns ``(verified, snapshot_if_rolled_back)``.
        """
        try:
            verified = bool(verify_fn())
        except Exception:
            logger.exception("verification raised for action %s; treating as failed", action_id)
            verified = False

        if verified:
            return True, None

        snap = self.restore(action_id, apply_fn=apply_fn)
        return False, snap
