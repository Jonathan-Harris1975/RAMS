"""Trusted GitHub release attestation from independent API reads.

This deliberately does not manufacture production deployment evidence.
"""
from __future__ import annotations

from typing import Any, Callable

from repo_mgmt.recovery_github_evidence import authenticated_release_checks
from repo_mgmt.recovery_merge_evidence import authenticated_merge
from repo_mgmt.deployment_receipt import verified_deployment


def authenticated_release_evidence(
    repository: str, pr_number: int, source_sha: str,
    github_get: Callable[[str], Any],
    deployment_get: Callable[[str], Any],
) -> dict[str, Any]:
    """Assemble verified CI, merge and deployed-SHA evidence or fail closed.

    deployment_get must query the authenticated production control plane for
    the supplied merge SHA; it must never read a PR body or model response.
    """
    checks = authenticated_release_checks(repository, source_sha, github_get)
    merge = authenticated_merge(repository, pr_number, source_sha, github_get)
    merged_sha = merge["merge_commit_sha"]
    deployment = verified_deployment(merged_sha, deployment_get(merged_sha))
    return {"checks": checks, "merge": merge, "deployment": deployment}
