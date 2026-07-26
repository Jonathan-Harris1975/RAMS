"""Git branch-safety and task rollback tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repo_mgmt.git_manager import BranchSafetyError, GitManager


def init_repo(path: Path, branch: str = "main") -> None:
    """Create a small Git repo with one committed file."""
    subprocess.run(
        ["git", "init", "-b", branch], cwd=path, check=True, stdout=subprocess.PIPE
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=path, check=True, stdout=subprocess.PIPE
    )


@pytest.mark.parametrize("branch", ["main", "master"])
def test_create_qa_branch_from_protected_branch_is_permitted(
    tmp_path: Path, branch: str
) -> None:
    init_repo(tmp_path, branch)
    gm = GitManager(tmp_path)
    created = gm.create_branch("rms-qa/on-brand/run-1")
    assert created == "rms-qa/on-brand/run-1"
    assert gm.current_branch() == "rms-qa/on-brand/run-1"


@pytest.mark.parametrize("branch", ["main", "master"])
def test_committing_on_protected_branch_remains_blocked(
    tmp_path: Path, branch: str
) -> None:
    init_repo(tmp_path, branch)
    gm = GitManager(tmp_path)
    (tmp_path / "README.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(BranchSafetyError):
        gm.stage_task_files(["README.md"])
    with pytest.raises(BranchSafetyError):
        gm.commit("blocked")


def test_live_patch_branch_name_includes_pipeline_and_run_id(tmp_path: Path) -> None:
    init_repo(tmp_path)
    gm = GitManager(tmp_path)
    branch = gm.make_qa_branch_name("mobile-ux", "2026-05-05T03-00-00Z")
    assert branch == "rms-qa/mobile-ux/2026-05-05T03-00-00Z"


def test_non_qa_branch_creation_is_blocked(tmp_path: Path) -> None:
    init_repo(tmp_path)
    with pytest.raises(BranchSafetyError):
        GitManager(tmp_path).create_branch("feature/random")


def test_dirty_tracked_file_fails_preflight_and_is_preserved(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "README.md").write_text("dirty\n", encoding="utf-8")
    gm = GitManager(tmp_path)
    with pytest.raises(Exception, match="dirty"):
        gm.assert_clean_worktree()
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "dirty\n"


def test_untracked_file_fails_preflight_and_is_preserved(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("operator notes\n", encoding="utf-8")
    gm = GitManager(tmp_path)
    with pytest.raises(Exception, match="dirty"):
        gm.assert_clean_worktree()
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "operator notes\n"


def test_task_restore_preserves_unrelated_untracked_file(tmp_path: Path) -> None:
    init_repo(tmp_path)
    gm = GitManager(tmp_path)
    gm.create_branch("rms-qa/on-brand/run-2")
    snapshot = gm.capture_task_state(["README.md"])
    (tmp_path / "README.md").write_text("task change\n", encoding="utf-8")
    (tmp_path / "unrelated.txt").write_text("keep me\n", encoding="utf-8")
    gm.restore_task_state(snapshot)
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "hello\n"
    assert (tmp_path / "unrelated.txt").read_text(encoding="utf-8") == "keep me\n"


def test_git_manager_timeout_raises_git_manager_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bounded Git timeouts are converted into safe GitManagerError failures."""
    from repo_mgmt import git_manager
    from repo_mgmt.git_manager import GitManagerError
    from repo_mgmt.process_runner import BoundedProcessResult

    monkeypatch.setattr(
        git_manager,
        "run_bounded",
        lambda *args, **kwargs: BoundedProcessResult(
            return_code=124,
            output="TIMEOUT after 30s",
            timed_out=True,
            truncated=False,
        ),
    )
    gm = GitManager(tmp_path)
    with pytest.raises(GitManagerError, match="timed out"):
        gm.status_porcelain()


def test_git_manager_passes_configured_output_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git commands use the configured diagnostic-output ceiling."""
    from repo_mgmt import git_manager
    from repo_mgmt.process_runner import BoundedProcessResult

    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> BoundedProcessResult:
        captured.update(kwargs)
        return BoundedProcessResult(0, "true", False, False)

    monkeypatch.setattr(git_manager, "run_bounded", fake_run)
    gm = GitManager(tmp_path, max_output_bytes=12_345)
    assert gm.is_git_repo() is True
    assert captured["max_output_bytes"] == 12_345


def test_push_uses_ephemeral_github_auth_header_without_putting_token_in_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Private-repo pushes authenticate without persisting credentials in origin."""
    from repo_mgmt import git_manager
    from repo_mgmt.process_runner import BoundedProcessResult

    captured: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command: list[str], **kwargs: object) -> BoundedProcessResult:
        env = dict(kwargs.get("env") or {})
        captured.append((list(command), env))
        if command[-3:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return BoundedProcessResult(0, "rms-qa/website/run-1", False, False)
        return BoundedProcessResult(0, "", False, False)

    monkeypatch.setattr(git_manager, "run_bounded", fake_run)
    gm = GitManager(tmp_path, push_enabled=True, github_token="secret-token")
    assert gm.push_branch("rms-qa/website/run-1") is True

    push_command, push_env = captured[-1]
    assert push_command[-4:] == ["push", "--set-upstream", "origin", "rms-qa/website/run-1"]
    assert "secret-token" not in " ".join(push_command)
    assert push_env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert push_env["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
