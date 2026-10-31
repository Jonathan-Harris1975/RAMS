"""Authenticated merge evidence regression tests."""
import pytest

from repo_mgmt.recovery_merge_evidence import authenticated_merge

HEAD = "a" * 40
MERGE = "b" * 40


def pr():
    return {"merged": True, "head": {"sha": HEAD}, "base": {"ref": "main"},
            "merge_commit_sha": MERGE}


def test_exact_head_merged_into_main():
    assert authenticated_merge("owner/RAMS", 12, HEAD, lambda _: pr()) == {
        "merged": True, "source_head_sha": HEAD, "merge_commit_sha": MERGE}


@pytest.mark.parametrize("field,value", [
    ("merged", False), ("head", {"sha": "c" * 40}),
    ("base", {"ref": "development"}), ("merge_commit_sha", None),
])
def test_stale_or_unmerged_pr_rejected(field, value):
    response = pr()
    response[field] = value
    with pytest.raises(ValueError):
        authenticated_merge("owner/RAMS", 12, HEAD, lambda _: response)


@pytest.mark.parametrize("number", [0, True, -1, 1000001])
def test_invalid_pr_number_fails_without_api_call(number):
    with pytest.raises(ValueError):
        authenticated_merge("owner/RAMS", number, HEAD,
                            lambda _: pytest.fail("unexpected API request"))
