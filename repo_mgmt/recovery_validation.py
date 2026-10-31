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
