"""Authenticated provider task reconciliation with independent task identity.

The caller owns a trusted provider API client and the original dispatch task ID.
An ambiguous response cannot release a durable accepted handoff.
"""
from __future__ import annotations

from typing import Any, Callable

from repo_mgmt.incident_identity import IncidentIdentity
from repo_mgmt.recovery_orchestrator import RecoveryOrchestrator
from repo_mgmt.recovery_validation import verified_provider_outcome


def reconcile_provider_task(
    orchestrator: RecoveryOrchestrator, identity: IncidentIdentity, *,
    owner: str, expected_task_id: str,
    provider_get: Callable[[str], Any],
    now: int | None = None,
) -> str:
    """Return pending/completed/reopened; only definitive failure reopens.

    provider_get must authenticate directly to the task provider. A 404,
    timeout, missing task ID, or provider exception propagates and leaves
    the durable handoff fenced.
    """
    if not isinstance(expected_task_id, str) or not expected_task_id:
        raise ValueError("original dispatch task ID is required")
    response = provider_get(expected_task_id)
    if not isinstance(response, dict):
        raise ValueError("invalid authenticated provider task response")
    if response.get("task_id") != expected_task_id:
        raise ValueError("provider task identity does not match handoff")
    if response.get("status") in {"queued", "running", "pending"}:
        return "pending"
    outcome = verified_provider_outcome(response, expected_task_id=expected_task_id)
    if outcome["status"] == "completed":
        return "completed"
    released = orchestrator.reconcile_failed_handoff(
        identity, owner=owner, provider_evidence=response,
        expected_task_id=expected_task_id, now=now,
    )
    if not released:
        raise RuntimeError("terminal failure verified but durable reconciliation failed")
    return "reopened"
