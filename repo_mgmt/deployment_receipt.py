"""Strict exact-SHA deployment verification for autonomous recovery receipts.

A successful CI check or a merged PR is not proof of deployment. Callers must
supply authenticated deployment metadata from the production control plane.
"""
from __future__ import annotations

import re
from typing import Any

_SHA = re.compile(r"[0-9a-f]{40}")


def verified_deployment(expected_sha: str, deployment: dict[str, Any]) -> dict[str, str]:
    """Return a minimal receipt only for a successful matching production release."""
    if not isinstance(expected_sha, str) or not _SHA.fullmatch(expected_sha):
        raise ValueError("invalid expected deployment SHA")
    if not isinstance(deployment, dict):
        raise ValueError("missing authenticated deployment metadata")
    actual = deployment.get("sha")
    if not isinstance(actual, str) or not _SHA.fullmatch(actual):
        raise ValueError("invalid deployed SHA")
    if actual != expected_sha:
        raise ValueError("production deployment does not match expected SHA")
    if deployment.get("environment") != "production" or deployment.get("status") != "success":
        raise ValueError("production deployment has not succeeded")
    provider = deployment.get("provider")
    identifier = deployment.get("deployment_id")
    if (not isinstance(provider, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", provider)
            or not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identifier)):
        raise ValueError("invalid deployment provenance")
    return {"sha": actual, "environment": "production", "status": "success",
            "provider": provider, "deployment_id": identifier}
