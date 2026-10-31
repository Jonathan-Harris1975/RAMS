"""Trusted GitHub release evidence must reject stale, missing and duplicate runs."""
import pytest

from repo_mgmt.recovery_github_evidence import authenticated_release_checks
from repo_mgmt.recovery_validation import REQUIRED

SHA = "a" * 40


def runs(sha=SHA):
    return [{"name": name, "head_sha": sha, "status": "completed",
             "conclusion": "success", "event": "pull_request"} for name in REQUIRED]


def test_all_exact_head_workflows_pass():
    paths = []
    result = authenticated_release_checks("owner/RAMS", SHA, lambda p: (
        paths.append(p) or {"workflow_runs": runs()}))
    assert len(result) == len(REQUIRED)
    assert f"head_sha={SHA}" in paths[0]


@pytest.mark.parametrize("change", [
    lambda x: x.pop(),
    lambda x: x.append(dict(x[0])),
    lambda x: x[0].update(head_sha="b" * 40),
    lambda x: x[0].update(conclusion="failure"),
    lambda x: x[0].update(status="in_progress"),
    lambda x: x[0].update(event="issue_comment"),
])
def test_untrusted_or_incomplete_evidence_fails_closed(change):
    evidence = runs()
    change(evidence)
    with pytest.raises(ValueError):
        authenticated_release_checks("owner/RAMS", SHA,
                                     lambda _: {"workflow_runs": evidence})


@pytest.mark.parametrize("repo", ["../RAMS", "owner/../repo", "owner", ""])
def test_invalid_repository_is_rejected_before_network(repo):
    def forbidden(_):
        raise AssertionError("network call must not occur")
    with pytest.raises(ValueError):
        authenticated_release_checks(repo, SHA, forbidden)
