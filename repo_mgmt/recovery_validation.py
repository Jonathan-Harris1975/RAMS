"""Strict, source-agnostic validation of authenticated exact-head release checks.

The caller must fetch check results from the trusted GitHub API. This module
never interprets PR-authored claims or untrusted workflow artifacts as proof.
"""
from __future__ import annotations

import re
from typing import Any

_SHA = re.compile(r"[0-9a-f]{40}")
REQUIRED = frozenset({"CI", "CodeQL", "Security and repository quality", "Item 6 hardening"})


def verified_release_checks(expected_sha: str, checks: Any) -> dict[str, str]:
    """Fail closed unless every mandatory check succeeded on the exact SHA."""
    if not isinstance(expected_sha, str) or not _SHA.fullmatch(expected_sha):
        raise ValueError("invalid expected release SHA")
    if not isinstance(checks, list) or len(checks) > 100:
        raise ValueError("missing or excessive authenticated release checks")
    accepted: dict[str, str] = {}
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("invalid release check")
        name = check.get("name")
        if name not in REQUIRED:
            continue
        if name in accepted:
            raise ValueError("ambiguous duplicate release check")
        if (check.get("head_sha") != expected_sha or check.get("status") != "completed"
                or check.get("conclusion") != "success"):
            raise ValueError("required release check is not green on the exact SHA")
        accepted[name] = expected_sha
    if set(accepted) != REQUIRED:
        raise ValueError("required exact-head release checks are missing")
    return accepted


def verified_merge(source_sha: str, merge: Any) -> str:
    """Validate trusted GitHub PR metadata tying a source head to a merged SHA.

    The caller must retrieve this metadata from GitHub, not from PR text.
    Squash and merge commits normally differ from the source head.
    """
    if not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha):
        raise ValueError("invalid source SHA")
    if not isinstance(merge, dict):
        raise ValueError("missing trusted merge evidence")
    deployed_sha = merge.get("merge_commit_sha")
    if (merge.get("merged") is not True or merge.get("source_head_sha") != source_sha
            or not isinstance(deployed_sha, str) or not _SHA.fullmatch(deployed_sha)):
        raise ValueError("invalid trusted exact-head merge evidence")
    return deployed_sha


def verified_provider_outcome(outcome: Any, *, expected_task_id: str) -> dict[str, str]:
    """Validate a terminal task response fetched by a trusted provider client.

    Never use webhook text or a model-authored repair summary as provenance.
    A missing, queued or running task is not a definitive failure.
    """
    if not isinstance(expected_task_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_.:-]{1,128}", expected_task_id
    ):
        raise ValueError("invalid expected provider task identity")
    if not isinstance(outcome, dict) or outcome.get("task_id") != expected_task_id:
        raise ValueError("provider task identity does not match handoff")
    status = outcome.get("status")
    if status not in {"failed", "completed"}:
        raise ValueError("provider outcome is not terminal")
    return {"task_id": expected_task_id, "status": status}
