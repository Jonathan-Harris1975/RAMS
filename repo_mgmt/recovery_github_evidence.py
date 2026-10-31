"""Fetch exact-head GitHub Actions evidence from the authenticated API.

Never infer success from a PR comment, a status badge or a previous SHA.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from repo_mgmt.recovery_validation import REQUIRED, verified_release_checks

_SHA = re.compile(r"[0-9a-f]{40}")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")


def authenticated_release_checks(
    repository: str, head_sha: str, api_get: Callable[[str], Any],
) -> list[dict[str, str]]:
    """Return validated, unambiguous completed workflow runs for one SHA.

    api_get must be an authenticated GitHub API client. Workflow run IDs and
    event names are read only from GitHub, never from an agent's claims.
    """
    if (not isinstance(repository, str) or not _REPO.fullmatch(repository)
            or any(part in {".", ".."} for part in repository.split("/"))
            or not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha)):
        raise ValueError("invalid repository or exact head SHA")
    result = api_get(
        f"/repos/{repository}/actions/runs?head_sha={head_sha}&per_page=100"
    )
    if not isinstance(result, dict) or not isinstance(result.get("workflow_runs"), list):
        raise ValueError("invalid authenticated workflow response")
    runs = result["workflow_runs"]
    if len(runs) > 100:
        raise ValueError("excessive workflow runs")
    checks = []
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("invalid workflow run")
        name = run.get("name")
        if name not in REQUIRED:
            continue
        if run.get("head_sha") != head_sha or run.get("event") not in {
            "pull_request", "push", "merge_group", "workflow_dispatch"
        }:
            continue
        if run.get("status") != "completed":
            raise ValueError("required workflow is not complete")
        checks.append({
            "name": name, "head_sha": head_sha,
            "status": run["status"], "conclusion": run.get("conclusion"),
        })
    verified_release_checks(head_sha, checks)
    return checks
