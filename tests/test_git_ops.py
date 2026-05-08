"""Tests for repo_mgmt.git_ops."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from repo_mgmt.git_ops import (
    BranchSafetyError,
    GitOpsError,
    ensure_clean_branch,
    stage_and_commit,
    push_branch,
    revert_to_head,
)


def _make_mock_repo(branch_name: str = "rms-qa/on-brand/2026-05-05") -> MagicMock:
    repo = MagicMock()
    active_branch = MagicMock()
    active_branch.name = branch_name
    type(repo).active_branch = PropertyMock(return_value=active_branch)
    repo.branches = [active_branch]
    return repo


class TestEnsureCleanBranch:
    def test_raises_on_main(self, tmp_path) -> None:
        with pytest.raises(BranchSafetyError, match="protected branch"):
            ensure_clean_branch(tmp_path, "main")

    def test_raises_on_master(self, tmp_path) -> None:
        with pytest.raises(BranchSafetyError, match="protected branch"):
            ensure_clean_branch(tmp_path, "master")


class TestStageAndCommit:
    def test_dry_run_returns_none(self) -> None:
        repo = _make_mock_repo()
        result = stage_and_commit(repo, ["index.html"], "test commit", dry_run=True)
        assert result is None
        repo.index.commit.assert_not_called()

    def test_raises_if_on_main(self) -> None:
        repo = _make_mock_repo(branch_name="main")
        with pytest.raises(BranchSafetyError):
            stage_and_commit(repo, ["index.html"], "test", dry_run=False)

    def test_commits_and_returns_sha(self) -> None:
        repo = _make_mock_repo()
        mock_commit = MagicMock()
        mock_commit.hexsha = "abc12345def67890"
        repo.index.commit.return_value = mock_commit
        result = stage_and_commit(repo, ["index.html"], "fix: add canonical", dry_run=False)
        assert result == "abc12345"


class TestPushBranch:
    def test_skipped_when_dry_run(self) -> None:
        repo = _make_mock_repo()
        push_branch(repo, "rms-qa/on-brand/2026-05-05", dry_run=True, push_enabled=True)
        repo.git.push.assert_not_called()

    def test_skipped_when_push_not_enabled(self) -> None:
        repo = _make_mock_repo()
        push_branch(repo, "rms-qa/on-brand/2026-05-05", dry_run=False, push_enabled=False)
        repo.git.push.assert_not_called()

    def test_pushes_when_enabled(self) -> None:
        repo = _make_mock_repo()
        push_branch(repo, "rms-qa/on-brand/2026-05-05", dry_run=False, push_enabled=True)
        repo.git.push.assert_called_once()

    def test_raises_on_protected_branch(self) -> None:
        repo = _make_mock_repo()
        with pytest.raises(BranchSafetyError):
            push_branch(repo, "main", dry_run=False, push_enabled=True)


class TestRevertToHead:
    def test_dry_run_does_not_reset(self) -> None:
        repo = _make_mock_repo()
        revert_to_head(repo, dry_run=True)
        repo.git.reset.assert_not_called()

    def test_resets_when_not_dry_run(self) -> None:
        repo = _make_mock_repo()
        revert_to_head(repo, dry_run=False)
        repo.git.reset.assert_called_once_with("--hard", "HEAD")



def test_ensure_clean_branch_blocks_when_active_branch_is_main(tmp_path):
    import subprocess
    from repo_mgmt import git_ops
    subprocess.run(['git','init','-b','main'],cwd=tmp_path,check=True,stdout=subprocess.PIPE)
    subprocess.run(['git','config','user.email','test@example.com'],cwd=tmp_path,check=True)
    subprocess.run(['git','config','user.name','Test'],cwd=tmp_path,check=True)
    (tmp_path/'README.md').write_text('hello')
    subprocess.run(['git','add','README.md'],cwd=tmp_path,check=True)
    subprocess.run(['git','commit','-m','init'],cwd=tmp_path,check=True,stdout=subprocess.PIPE)
    with pytest.raises(git_ops.BranchSafetyError): git_ops.ensure_clean_branch(tmp_path,'rms-qa/test')
