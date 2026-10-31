"""Authenticated GitHub merge metadata for an exact source head.

Only the trusted GitHub API response may supply the merge commit identity.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from repo_mgmt.recovery_validation import verified_merge

_SHA = re.compile(r"[0-9a-f]{40}")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")


def authenticated_merge(repository: str, pr_number: int, source_sha: str,
                        api_get: Callable[[str], Any]) -> dict[str, Any]:
    if (not isinstance(repository, str) or not _REPO.fullmatch(repository)
            or any(p in {".", ".."} for p in repository.split("/"))
            or type(pr_number) is not int or not 1 <= pr_number <= 1_000_000
            or not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha)):
        raise ValueError("invalid merge lookup")
    pr = api_get(f"/repos/{repository}/pulls/{pr_number}")
    if not isinstance(pr, dict):
        raise ValueError("invalid authenticated PR response")
    head = pr.get("head")
    base = pr.get("base")
    if (not isinstance(head, dict) or not isinstance(base, dict)
            or head.get("sha") != source_sha or base.get("ref") != "main"
            or pr.get("merged") is not True):
        raise ValueError("PR has not merged the expected head into main")
    evidence = {"merged": True, "source_head_sha": source_sha,
                "merge_commit_sha": pr.get("merge_commit_sha")}
    verified_merge(source_sha, evidence)
    return evidence
