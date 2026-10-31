"""Release attestation cannot confuse CI or merge with a deployment."""
import pytest

from repo_mgmt.recovery_release import authenticated_release_evidence
from repo_mgmt.recovery_validation import REQUIRED

HEAD = "a" * 40
MERGED = "b" * 40


def github(path):
    if "/actions/runs?" in path:
        return {"workflow_runs": [
            {"name": name, "head_sha": HEAD, "status": "completed",
             "conclusion": "success", "event": "pull_request"} for name in REQUIRED
        ]}
    return {"merged": True, "head": {"sha": HEAD}, "base": {"ref": "main"},
            "merge_commit_sha": MERGED}


def deployment(sha):
    return {"sha": sha, "environment": "production", "status": "success",
            "provider": "koyeb", "deployment_id": "deployment-1"}


def test_complete_authenticated_release():
    evidence = authenticated_release_evidence("owner/RAMS", 5, HEAD, github, deployment)
    assert evidence["merge"]["merge_commit_sha"] == MERGED
    assert evidence["deployment"]["sha"] == MERGED


def test_wrong_production_sha_fails_closed():
    with pytest.raises(ValueError, match="does not match"):
        authenticated_release_evidence(
            "owner/RAMS", 5, HEAD, github,
            lambda _: deployment("c" * 40))


def test_missing_production_attestation_fails_closed():
    with pytest.raises(ValueError):
        authenticated_release_evidence("owner/RAMS", 5, HEAD, github, lambda _: None)
