#!/usr/bin/env python3
"""Keep trusted repair PRs on the repository's Mergify-only merge path.

This runs from the trusted default-branch workflow. It reads PR metadata only,
never checks out or executes PR code, and removes GitHub native auto-merge
requests for PRs admitted to the Mergify lifecycle.
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.github.com"
TOKEN = os.environ.get("NATIVE_MERGE_TOKEN", "")
REPO = os.environ.get("REPO", "")
DEFAULT_BRANCH = os.environ.get("DEFAULT_BRANCH", "main")
ADMISSION_CHECK = "Trusted autonomy admission"

def request(method: str, path: str, data: dict[str, Any] | None = None) -> Any:
    url = path if path.startswith("http") else f"{API}{path}"
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"GitHub API HTTP {exc.code}: {message[:500]}") from exc


def graphql(query: str, variables: dict[str, Any]) -> Any:
    result = request("POST", "https://api.github.com/graphql", {"query": query, "variables": variables})
    if result.get("errors"):
        raise RuntimeError(f"GitHub GraphQL error: {result['errors']}")
    return result.get("data", {})


def pages(path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in range(1, 11):
        batch = request("GET", f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}") or []
        items.extend(batch)
        if len(batch) < 100:
            return items
    raise RuntimeError("Open PR listing exceeded the 1,000-item safety limit")


def admission_check(number: int, sha: str) -> tuple[bool, bool]:
    """Return (exact successful admission exists, any admission check exists)."""
    name = urllib.parse.quote(ADMISSION_CHECK, safe="")
    runs = request(
        "GET",
        f"/repos/{REPO}/commits/{sha}/check-runs?check_name={name}&filter=all&per_page=100",
    ).get("check_runs", [])
    exact_id = f"trusted-autonomy-admission:{number}:{sha}"
    exact_runs = [
        run for run in runs
        if (run.get("app") or {}).get("slug") == "github-actions"
        and run.get("name") == ADMISSION_CHECK
        and run.get("external_id") == exact_id
    ]
    success = any(
        run.get("status") == "completed" and run.get("conclusion") == "success"
        for run in exact_runs
    )
    return success, bool(exact_runs)


def merge_request(pr_node_id: str) -> dict[str, Any] | None:
    result = graphql(
        "query($id: ID!) { node(id: $id) { ... on PullRequest { autoMergeRequest { enabledAt mergeMethod } } } }",
        {"id": pr_node_id},
    )
    node = result.get("node") or {}
    return node.get("autoMergeRequest")


def disable(pr_node_id: str) -> None:
    graphql(
        "mutation($id: ID!) { disablePullRequestAutoMerge(input: {pullRequestId: $id}) { pullRequest { number } } }",
        {"id": pr_node_id},
    )


def reconcile(pr_summary: dict[str, Any]) -> None:
    number = int(pr_summary["number"])
    labels = {label["name"] for label in pr_summary.get("labels", [])}
    node_id = str(pr_summary.get("node_id") or "")
    if not node_id:
        return
    active_request = merge_request(node_id)

    # Leave unrelated user-managed PRs alone. Trusted repair PRs are merged
    # only by Mergify after exact-head admission; this workflow never arms the
    # GitHub native auto-merge path.
    if "autonomy:admitted" not in labels and active_request is None:
        return

    pr = request("GET", f"/repos/{REPO}/pulls/{number}")
    current_labels = {label["name"] for label in pr.get("labels", [])}
    sha = str((pr.get("head") or {}).get("sha") or "")
    _, has_admission_evidence = admission_check(number, sha) if sha else (False, False)

    if active_request is not None and (
        "autonomy:admitted" in current_labels or has_admission_evidence
    ):
        disable(node_id)
        print(
            f"Disabled GitHub native auto-merge for trusted PR #{number}; "
            "Mergify is the repository's merge authority."
        )


def main() -> int:
    if not REPO or not TOKEN:
        raise RuntimeError("REPO and NATIVE_MERGE_TOKEN are required")
    prs = pages(f"/repos/{REPO}/pulls?state=open")
    for pr in prs:
        reconcile(pr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
