"""Trusted PR repair incident coordination adapter.

This is deliberately opt-in: no unconfigured workflow silently acquires a
different deduplication authority. The caller must supply the existing shared
D1 coordinator and R2 evidence client, and still retain GitHub reservations.
"""
from __future__ import annotations

import hashlib
import json
import re

from repo_mgmt.incident_identity import IncidentIdentity
from repo_mgmt.recovery_orchestrator import RecoveryOrchestrator

_SHA = re.compile(r"^[0-9a-f]{40}$")
_KIND = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


def repair_identity(repository: str, pr_number: int, head_sha: str,
                    kind: str, findings: list[str]) -> IncidentIdentity:
    """Fingerprint the authenticated issue, never an arbitrary repair prompt."""
    if isinstance(pr_number, bool) or not isinstance(pr_number, int) or not 1 <= pr_number <= 1_000_000:
        raise ValueError("invalid PR number")
    if not _SHA.fullmatch(head_sha) or not _KIND.fullmatch(kind):
        raise ValueError("invalid repair head or category")
    if not isinstance(findings, list) or not 1 <= len(findings) <= 12 or any(
        not isinstance(finding, str) or not 1 <= len(finding) <= 3500
        for finding in findings
    ):
        raise ValueError("invalid bounded findings")
    # Avoid transient URLs and order-dependent incidental whitespace in identity.
    canonical = sorted(set(" ".join(finding.split()) for finding in findings))
    digest = hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()
    return IncidentIdentity(repository, "pr_repair", kind,
                            f"pr:{pr_number}:{digest}", head_sha)


def reserve_repair(orchestrator: RecoveryOrchestrator, *,
                   repository: str, pr_number: int, head_sha: str,
                   kind: str, findings: list[str], owner: str) -> tuple[IncidentIdentity, bool]:
    """Require durable claim and evidence before a trusted webhook handoff."""
    identity = repair_identity(repository, pr_number, head_sha, kind, findings)
    acquired = orchestrator.claim(identity, owner=owner, diagnosis={
        "pr_number": pr_number,
        "kind": kind,
        "findings": findings,
        "source_head_sha": head_sha,
    })
    return identity, acquired
