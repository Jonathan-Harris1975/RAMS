"""Retired in-process scheduler entry point for RAMS.

Cron-style execution is intentionally disabled. Deployments trigger pipelines
externally through the FastAPI endpoints instead, which keeps Koyeb web
services from launching unattended background runs.
"""

from __future__ import annotations

from typing import Any


def build_scheduler(*_args: Any, **_kwargs: Any) -> None:
    """Reject attempts to create the removed in-process cron scheduler."""
    raise RuntimeError(
        "RAMS in-process cron scheduling has been removed; trigger pipelines "
        "externally via POST /rebuild/{pipeline_id}/run."
    )
