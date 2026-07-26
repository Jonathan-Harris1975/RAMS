"""GitHub pull-request publication for validated RAMS remediation branches."""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


class GitHubPullRequestError(RuntimeError):
    """Raised when RAMS cannot resolve or create the required pull request."""


@dataclass(frozen=True)
class PullRequestResult:
    """Public pull-request metadata safe to persist in RAMS run reports."""

    number: int
    url: str
    title: str
    base: str
    head: str
    created: bool


def parse_github_repo(url: str) -> tuple[str, str]:
    """Return ``(owner, repo)`` for a GitHub HTTPS repository URL."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise GitHubPullRequestError(
            "automatic PR creation requires an https://github.com/<owner>/<repo> repository URL"
        )
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise GitHubPullRequestError(
            "automatic PR creation requires a repository URL with exactly owner/repo"
        )
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        raise GitHubPullRequestError("GitHub repository owner/name could not be resolved")
    return owner, repo


def _response_error(response: httpx.Response) -> str:
    """Return a bounded GitHub API failure description without credentials."""
    message = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            message = str(payload.get("message") or "")
    except Exception:
        message = ""
    if not message:
        message = response.text.strip()
    if len(message) > 1_000:
        message = message[:1_000] + "…"
    return f"GitHub API HTTP {response.status_code}: {message or 'request failed'}"


def _request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_retries: int,
    headers: dict[str, str] | None = None,
    params: dict[str, str | int] | None = None,
    json: dict[str, object] | None = None,
) -> httpx.Response:
    """Issue a GitHub request with bounded retry for transient failures only."""
    attempts = max_retries + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
            )
            if response.status_code not in {429, 500, 502, 503, 504}:
                return response
            if attempt == attempts - 1:
                return response
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
        time.sleep(min(2**attempt, 4))
    raise GitHubPullRequestError(
        f"GitHub API request failed after {attempts} attempt(s): {last_error}"
    )


def _find_open_pr(
    client: httpx.Client,
    *,
    api_base: str,
    owner: str,
    repo: str,
    base: str,
    head: str,
    max_retries: int,
    headers: dict[str, str],
) -> dict[str, object] | None:
    """Return an existing open PR for this exact head/base pair when present."""
    url = f"{api_base.rstrip('/')}/repos/{owner}/{repo}/pulls"
    response = _request_with_retry(
        client,
        "GET",
        url,
        max_retries=max_retries,
        params={"state": "open", "head": f"{owner}:{head}", "base": base, "per_page": 10},
        headers=headers,
    )
    if response.status_code != 200:
        raise GitHubPullRequestError(_response_error(response))
    payload = response.json()
    if not isinstance(payload, list):
        raise GitHubPullRequestError("GitHub API returned an invalid pull-request list")
    for item in payload:
        if isinstance(item, dict):
            return item
    return None


def _to_result(payload: dict[str, object], *, created: bool) -> PullRequestResult:
    """Validate the subset of GitHub PR response fields RAMS persists."""
    number = payload.get("number")
    url = payload.get("html_url")
    title = payload.get("title")
    base_obj = payload.get("base")
    head_obj = payload.get("head")
    if not isinstance(number, int) or not isinstance(url, str) or not url.startswith("https://"):
        raise GitHubPullRequestError("GitHub API pull-request response is missing number/html_url")
    base_ref = base_obj.get("ref") if isinstance(base_obj, dict) else None
    head_ref = head_obj.get("ref") if isinstance(head_obj, dict) else None
    if not isinstance(base_ref, str) or not isinstance(head_ref, str):
        raise GitHubPullRequestError("GitHub API pull-request response is missing branch refs")
    return PullRequestResult(
        number=number,
        url=url,
        title=str(title or "RAMS automated remediation"),
        base=base_ref,
        head=head_ref,
        created=created,
    )


def create_or_get_pull_request(
    *,
    token: str | None,
    repo_url: str,
    base_branch: str,
    head_branch: str,
    title: str,
    body: str,
    api_base: str = "https://api.github.com",
    timeout_seconds: float = 20.0,
    max_retries: int = 2,
    client: httpx.Client | None = None,
) -> PullRequestResult:
    """Create one non-draft PR for a pushed RAMS QA branch, idempotently."""
    token_value = str(token or "").strip()
    if not token_value:
        raise GitHubPullRequestError("automatic PR creation requires RMS_GITHUB_TOKEN/GITHUB_TOKEN")
    if not base_branch.strip() or not head_branch.strip():
        raise GitHubPullRequestError("automatic PR creation requires non-empty base/head branches")
    if base_branch == head_branch:
        raise GitHubPullRequestError("automatic PR creation refuses identical base and head branches")

    owner, repo = parse_github_repo(repo_url)
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token_value}",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "RAMS/1.1",
    }
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout_seconds, headers=headers, follow_redirects=False)
    try:
        existing = _find_open_pr(
            http,
            api_base=api_base,
            owner=owner,
            repo=repo,
            base=base_branch,
            head=head_branch,
            max_retries=max_retries,
            headers=headers,
        )
        if existing is not None:
            return _to_result(existing, created=False)

        endpoint = f"{api_base.rstrip('/')}/repos/{owner}/{repo}/pulls"
        response = _request_with_retry(
            http,
            "POST",
            endpoint,
            max_retries=max_retries,
            headers=headers,
            json={
                "title": title,
                "head": head_branch,
                "base": base_branch,
                "body": body,
                "maintainer_can_modify": True,
                "draft": False,
            },
        )
        if response.status_code == 201:
            payload = response.json()
            if not isinstance(payload, dict):
                raise GitHubPullRequestError("GitHub API returned an invalid create-PR response")
            return _to_result(payload, created=True)

        # A retried request can race with successful PR creation. Resolve the
        # canonical open PR before treating GitHub's 422 as a hard failure.
        if response.status_code == 422:
            existing = _find_open_pr(
                http,
                api_base=api_base,
                owner=owner,
                repo=repo,
                base=base_branch,
                head=head_branch,
                max_retries=max_retries,
                headers=headers,
            )
            if existing is not None:
                return _to_result(existing, created=False)
        raise GitHubPullRequestError(_response_error(response))
    finally:
        if owns_client:
            http.close()
