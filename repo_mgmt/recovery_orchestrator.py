"""Durable recovery orchestration across D1 claims and R2 evidence.

No repair dispatch occurs without a verified D1 claim. R2 write failure
leaves the lease in place until it expires; it is never silently released.
"""
from __future__ import annotations

import time
from typing import Any

from repo_mgmt.d1_coordinator import D1Coordinator
from repo_mgmt.incident_identity import IncidentIdentity
from repo_mgmt.recovery_evidence import write_evidence
from repo_mgmt.deployment_receipt import verified_deployment
from repo_mgmt.recovery_validation import verified_release_checks, verified_merge, verified_provider_outcome


class RecoveryOrchestrator:
    def __init__(self, coordinator: D1Coordinator, r2_client: Any) -> None:
        self.coordinator = coordinator
        self.r2_client = r2_client

    def claim(self, identity: IncidentIdentity, *, owner: str,
              diagnosis: dict[str, Any], now: int | None = None) -> bool:
        """Acquire one bounded attempt and persist diagnostic evidence."""
        timestamp = int(time.time()) if now is None else now
        acquired = self.coordinator.acquire(
            identity.incident_key, identity.attempt_key, identity.repository,
            identity.head_sha.lower(), owner, now=timestamp,
        )
        if not acquired:
            return False
        # Fail closed: an unavailable evidence store must not trigger dispatch.
        write_evidence(
            self.r2_client, repo=identity.repository.split("/")[1],
            kind="incidents", incident_key=identity.incident_key,
            head_sha=identity.head_sha, record={
                "attempt_key": identity.attempt_key,
                "owner": owner, "diagnosis": diagnosis, "state": "claimed",
            },
        )
        if not self.coordinator.record_event(
            identity.incident_key, identity.attempt_key, owner,
            "claim_acquired", now=timestamp,
        ):
            raise RuntimeError("D1 claim audit was not persisted")
        return True

    def renew(self, identity: IncidentIdentity, *, owner: str,
              now: int | None = None) -> bool:
        """Heartbeat the lease for the same head, owner and attempt."""
        timestamp = int(time.time()) if now is None else now
        renewed = self.coordinator.renew(
            identity.incident_key, identity.attempt_key, owner, now=timestamp,
        )
        if renewed:
            if not self.coordinator.record_event(
                identity.incident_key, identity.attempt_key, owner,
                "lease_renewed", now=timestamp,
            ):
                raise RuntimeError("D1 lease renewal audit was not persisted")
        return renewed

    def reconcile_failed_handoff(self, identity: IncidentIdentity, *,
                                 owner: str, provider_evidence: dict[str, Any],
                                 expected_task_id: str, now: int | None = None) -> bool:
        """Reopen a handed-off attempt only on definitive provider failure.

        A timeout, missing task, or transport error is not terminal evidence.
        This is a trusted-runtime API; callers must authenticate provenance.
        """
        # Expected identity must come from the original trusted dispatch receipt,
        # never from the provider payload under examination.
        outcome = verified_provider_outcome(provider_evidence, expected_task_id=expected_task_id)
        if outcome["status"] != "failed":
            raise ValueError("verified terminal provider failure is required")
        timestamp = int(time.time()) if now is None else now
        write_evidence(
            self.r2_client, repo=identity.repository.split("/")[1],
            kind="receipts", incident_key=identity.incident_key,
            head_sha=identity.head_sha, record={
                "attempt_key": identity.attempt_key, "owner": owner,
                "reconciliation": "provider_terminal_failure",
                "task_id": expected_task_id,
            },
        )
        released = self.coordinator.reconcile_failed_handoff(
            identity.incident_key, identity.attempt_key, owner, now=timestamp,
        )
        if released and not self.coordinator.record_event(
            identity.incident_key, identity.attempt_key, owner,
            "provider_handoff_failed", now=timestamp,
        ):
            raise RuntimeError("D1 reconciliation audit was not persisted")
        return released

    def finish(self, identity: IncidentIdentity, *, owner: str,
               resolved: bool, evidence: dict[str, Any],
               now: int | None = None) -> bool:
        """Persist the outcome before releasing the exact owner's lease."""
        if type(resolved) is not bool:
            raise ValueError("resolved must be boolean")
        if not isinstance(evidence, dict):
            raise ValueError("completion evidence must be an object")
        if resolved:
            # Only authenticated exact-SHA production provenance can close an
            # incident. Passing CI or merging a PR is insufficient.
            verified_release_checks(identity.head_sha.lower(), evidence.get("checks"))
            merged_sha = verified_merge(identity.head_sha.lower(), evidence.get("merge"))
            deployment = evidence.get("deployment")
            if not isinstance(deployment, dict):
                raise ValueError("missing production deployment evidence")
            verified_deployment(merged_sha, deployment)
        timestamp = int(time.time()) if now is None else now
        # A completion receipt is an attempted outcome, not proof that the
        # coordinator accepted a release. The exact-owner fence remains decisive.
        write_evidence(
            self.r2_client, repo=identity.repository.split("/")[1],
            kind="receipts", incident_key=identity.incident_key,
            head_sha=identity.head_sha, record={
                "attempt_key": identity.attempt_key,
                "owner": owner, "resolved": resolved, "evidence": evidence,
            },
        )
        released = self.coordinator.finish(
            identity.incident_key, identity.attempt_key, owner,
            resolved=resolved, now=timestamp,
        )
        if released:
            if not self.coordinator.record_event(
                identity.incident_key, identity.attempt_key, owner,
                "repair_resolved" if resolved else "repair_released",
                now=timestamp,
            ):
                raise RuntimeError("D1 completion audit was not persisted")
        return released
