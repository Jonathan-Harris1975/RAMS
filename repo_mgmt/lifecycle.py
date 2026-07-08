"""Service lifecycle state model for RAMS.

RAMS cannot know it is about to be paused (Koyeb suspends the instance outright), so
"standby" is authoritatively tracked by MAST, the actor that pauses/resumes RAMS via
the Koyeb API. This module only owns the states RAMS *can* observe about itself while
its process is actually running: starting (boot grace period), online (idle and
ready), busy (a pipeline is actively running), and maintenance (operator-toggled).
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

VALID_STATES = ("starting", "online", "busy", "standby", "offline", "maintenance")

_PROCESS_STARTED_MONOTONIC = time.monotonic()
_STARTUP_GRACE_SECONDS = float(os.environ.get("RMS_STARTUP_GRACE_SECONDS", "20"))

_current: dict[str, Any] = {
    "state": "starting",
    "since": datetime.now(UTC).isoformat(),
    "reason": "process-boot",
}
_maintenance: dict[str, Any] = {"on": False, "reason": None, "since": None}


def _set(value: str, reason: str) -> None:
    if _current["state"] == value:
        return
    _current["state"] = value
    _current["since"] = datetime.now(UTC).isoformat()
    _current["reason"] = reason


def enter_maintenance(reason: str = "operator-requested") -> dict[str, Any]:
    """Force RAMS into Maintenance regardless of live signals until cleared."""
    _maintenance["on"] = True
    _maintenance["reason"] = reason
    _maintenance["since"] = datetime.now(UTC).isoformat()
    _set("maintenance", reason)
    return snapshot()


def exit_maintenance(reason: str = "operator-cleared") -> dict[str, Any]:
    _maintenance["on"] = False
    _maintenance["reason"] = None
    _maintenance["since"] = None
    _set("starting", reason)
    return snapshot()


def is_in_maintenance() -> bool:
    return bool(_maintenance["on"])


def compute_state(*, busy: bool, dependencies_ready: bool) -> dict[str, Any]:
    """Recompute the lifecycle snapshot from the caller's live signals.

    `busy` should reflect whether any pipeline is currently running (admission
    tracking already exists in api.py via `_running`/`_active_pipeline`).
    `dependencies_ready` should reflect the existing readiness/dependency check.
    """
    if _maintenance["on"]:
        value = "maintenance"
    elif busy:
        value = "busy"
    elif not dependencies_ready:
        value = "starting"
    elif (time.monotonic() - _PROCESS_STARTED_MONOTONIC) < _STARTUP_GRACE_SECONDS:
        value = "starting"
    else:
        value = "online"

    reason = {
        "maintenance": _maintenance.get("reason") or "operator-requested",
        "busy": "pipeline-running",
        "starting": "dependencies-not-ready" if not dependencies_ready else "startup-grace-period",
        "online": "ready",
    }[value]
    _set(value, reason)
    return snapshot()


def snapshot() -> dict[str, Any]:
    return {
        "state": _current["state"],
        "since": _current["since"],
        "reason": _current["reason"],
        "uptimeSeconds": round(time.monotonic() - _PROCESS_STARTED_MONOTONIC, 1),
        "maintenance": dict(_maintenance),
    }
