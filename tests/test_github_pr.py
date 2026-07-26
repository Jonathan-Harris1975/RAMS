"""Automatic GitHub pull-request publication tests."""

from __future__ import annotations

import json

import httpx
import pytest

from repo_mgmt.github_pr import (
    GitHubPullRequestError,
    create_or_get_pull_request,
    parse_github_repo,
)


def _pr_payload(*, number: int = 12, head: str = "rms-qa/website/run-1") -> dict:
    return {
        "number": number,
        "html_url": f"https://github.com/example/site/pull/{number}",
        "title": "RAMS website remediation",
        "base": {"ref": "main"},
        "head": {"ref": head},
    }


def test_parse_github_repo_accepts_https_git_suffix() -> None:
    assert parse_github_repo("https://github.com/example/site.git") == ("example", "site")


def test_parse_github_repo_rejects_non_github_url() -> None:
    with pytest.raises(GitHubPullRequestError, match="github.com"):
        parse_github_repo("https://gitlab.com/example/site.git")


def test_existing_open_pr_is_reused_without_post() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        assert request.headers["authorization"] == "Bearer token"
        assert request.url.params["head"] == "example:rms-qa/website/run-1"
        return httpx.Response(200, json=[_pr_payload()])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = create_or_get_pull_request(
            token="token",
            repo_url="https://github.com/example/site.git",
            base_branch="main",
            head_branch="rms-qa/website/run-1",
            title="RAMS website remediation",
            body="body",
            max_retries=0,
            client=client,
        )
    finally:
        client.close()

    assert methods == ["GET"]
    assert result.number == 12
    assert result.created is False


def test_new_pr_is_created_after_exact_open_pr_lookup() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        assert request.headers["authorization"] == "Bearer token"
        if request.method == "GET":
            return httpx.Response(200, json=[])
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["head"] == "rms-qa/website/run-1"
        assert payload["base"] == "main"
        assert payload["draft"] is False
        return httpx.Response(201, json=_pr_payload(number=27))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = create_or_get_pull_request(
            token="token",
            repo_url="https://github.com/example/site",
            base_branch="main",
            head_branch="rms-qa/website/run-1",
            title="RAMS website remediation",
            body="body",
            max_retries=0,
            client=client,
        )
    finally:
        client.close()

    assert [method for method, _ in requests] == ["GET", "POST"]
    assert result.number == 27
    assert result.created is True
    assert result.url.endswith("/pull/27")


def test_pr_creation_requires_token() -> None:
    with pytest.raises(GitHubPullRequestError, match="RMS_GITHUB_TOKEN"):
        create_or_get_pull_request(
            token=None,
            repo_url="https://github.com/example/site",
            base_branch="main",
            head_branch="rms-qa/website/run-1",
            title="x",
            body="y",
        )
