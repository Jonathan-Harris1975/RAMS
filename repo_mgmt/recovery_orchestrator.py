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

    def finish(self, identity: IncidentIdentity, *, owner: str,
               resolved: bool, evidence: dict[str, Any],
               now: int | None = None) -> bool:
        """Persist the outcome before releasing the exact owner's lease."""
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
