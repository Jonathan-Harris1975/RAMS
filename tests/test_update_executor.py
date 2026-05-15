"""Transactional live-task tests for update_executor."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import MethodType
from unittest.mock import MagicMock, patch

from repo_mgmt import update_executor
from repo_mgmt.git_manager import GitManager
from repo_mgmt.validation_runner import ValidationResult


def init_git_repo(repo: Path) -> GitManager:
    """Create a Git repo on a QA branch with a patchable index.html."""
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "index.html").write_text(
        "<html><head><title>Old</title></head><body></body></html>",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "index.html"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    gm = GitManager(repo)
    gm.create_branch("rms-qa/on-brand/run-1")
    return gm


def issue(sample_audit: dict) -> dict:
    """Return a normalised-enough code_fix issue for executor tests."""
    return {
        **sample_audit["findings"][0],
        "taskId": "t1",
        "classification": "code_fix",
        "status": "pending",
    }


def read_index(repo: Path) -> str:
    """Read index.html from a test repo."""
    return (repo / "index.html").read_text(encoding="utf-8")


def run_executor(
    issue_doc: dict,
    repo: Path,
    settings,
    mock_router,
    gm: GitManager | None,
    dry_run: bool,
) -> dict:
    """Run update_executor.run_task synchronously."""
    return asyncio.run(
        update_executor.run_task(
            issue_doc, repo, "on-brand", settings, mock_router, gm, dry_run
        )
    )


def test_update_executor_happy_path_in_dry_run(
    settings, mock_router, sample_audit, tmp_repo
) -> None:
    result = run_executor(issue(sample_audit), tmp_repo, settings, mock_router, None, True)
    assert result["status"] == "planned"
    assert result["patch"]["patchProtocol"] == "AnchorPatch/v1"


def test_validation_failure_restores_task_changes(
    settings, mock_router, sample_audit, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    gm = init_git_repo(repo)
    failed = ValidationResult(
        passed=False, commands=["false"], output_tail="bad", failed_command="false"
    )
    with patch("repo_mgmt.update_executor.validation_runner.run", return_value=failed):
        result = run_executor(issue(sample_audit), repo, settings, mock_router, gm, False)
    assert result["status"] == "manual_review"
    assert result["reverted"] is True
    assert "validation failed" in result["error"]
    assert "<title>Old</title>" in read_index(repo)
    assert gm.is_worktree_clean()


def test_validation_failure_restores_even_when_revert_flag_disabled(
    settings, mock_router, sample_audit, tmp_path: Path
) -> None:
    settings.rms_revert_on_validation_failure = False
    repo = tmp_path / "repo"
    repo.mkdir()
    gm = init_git_repo(repo)
    failed = ValidationResult(
        passed=False, commands=["false"], output_tail="bad", failed_command="false"
    )
    with patch("repo_mgmt.update_executor.validation_runner.run", return_value=failed):
        result = run_executor(issue(sample_audit), repo, settings, mock_router, gm, False)
    assert result["status"] == "manual_review"
    assert "<title>Old</title>" in read_index(repo)
    assert gm.is_worktree_clean()


def test_validation_runner_exception_restores_task_changes(
    settings, mock_router, sample_audit, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    gm = init_git_repo(repo)
    with patch(
        "repo_mgmt.update_executor.validation_runner.run",
        side_effect=RuntimeError("validator exploded"),
    ):
        result = run_executor(issue(sample_audit), repo, settings, mock_router, gm, False)
    assert result["status"] == "manual_review"
    assert "validator exploded" in result["error"]
    assert "<title>Old</title>" in read_index(repo)
    assert gm.is_worktree_clean()


def test_stage_failure_restores_task_changes(
    settings, mock_router, sample_audit, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    gm = init_git_repo(repo)
    with patch(
        "repo_mgmt.update_executor.validation_runner.run",
        return_value=ValidationResult(True, ["true"], "ok"),
    ):
        gm.stage_task_files = MethodType(  # type: ignore[method-assign]
            lambda self, paths: (_ for _ in ()).throw(RuntimeError("stage failed")),
            gm,
        )
        result = run_executor(issue(sample_audit), repo, settings, mock_router, gm, False)
    assert result["status"] == "manual_review"
    assert "stage failed" in result["error"]
    assert "<title>Old</title>" in read_index(repo)
    assert gm.is_worktree_clean()


def test_commit_failure_restores_task_changes(
    settings, mock_router, sample_audit, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    gm = init_git_repo(repo)
    with patch(
        "repo_mgmt.update_executor.validation_runner.run",
        return_value=ValidationResult(True, ["true"], "ok"),
    ):
        gm.commit = MethodType(  # type: ignore[method-assign]
            lambda self, message: (_ for _ in ()).throw(RuntimeError("commit failed")),
            gm,
        )
        result = run_executor(issue(sample_audit), repo, settings, mock_router, gm, False)
    assert result["status"] == "manual_review"
    assert "commit failed" in result["error"]
    assert "<title>Old</title>" in read_index(repo)
    assert gm.is_worktree_clean()


def test_push_failure_resets_task_commit_and_restores_clean_tree(
    settings, mock_router, sample_audit, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    gm = init_git_repo(repo)
    head_before = gm.current_head()
    with patch(
        "repo_mgmt.update_executor.validation_runner.run",
        return_value=ValidationResult(True, ["true"], "ok"),
    ):
        gm.push_branch = MethodType(  # type: ignore[method-assign]
            lambda self, branch: (_ for _ in ()).throw(RuntimeError("push failed")),
            gm,
        )
        result = run_executor(issue(sample_audit), repo, settings, mock_router, gm, False)
    assert result["status"] == "manual_review"
    assert "push failed" in result["error"]
    assert gm.current_head() == head_before
    assert "<title>Old</title>" in read_index(repo)
    assert gm.is_worktree_clean()


def test_post_apply_failure_preserves_unrelated_untracked_file(
    settings, mock_router, sample_audit, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    gm = init_git_repo(repo)
    unrelated = repo / "operator-notes.txt"
    unrelated.write_text("keep\n", encoding="utf-8")
    with patch(
        "repo_mgmt.update_executor.validation_runner.run",
        side_effect=RuntimeError("after apply"),
    ):
        result = run_executor(issue(sample_audit), repo, settings, mock_router, gm, False)
    assert result["status"] == "manual_review"
    assert unrelated.read_text(encoding="utf-8") == "keep\n"
    assert "<title>Old</title>" in read_index(repo)


def test_magicmock_git_manager_restores_via_task_snapshot(
    settings, mock_router, sample_audit, tmp_repo
) -> None:
    gm = MagicMock(unsafe=True)
    gm.assert_write_allowed.return_value = None
    gm.capture_task_state.return_value = object()
    failed = ValidationResult(
        passed=False, commands=["false"], output_tail="bad", failed_command="false"
    )
    with patch("repo_mgmt.update_executor.validation_runner.run", return_value=failed):
        result = run_executor(issue(sample_audit), tmp_repo, settings, mock_router, gm, False)
    gm.restore_task_state.assert_called_once_with(gm.capture_task_state.return_value)
    assert result["status"] == "manual_review"


def test_partial_patch_snapshot_candidates_include_generated_html(tmp_path: Path) -> None:
    """Partial patches must capture generated pages before post-patch sync."""
    repo = tmp_path / "site"
    (repo / "assets" / "partials").mkdir(parents=True)
    (repo / "assets" / "partials" / "header.html").write_text("<header/>", encoding="utf-8")
    (repo / "index.html").write_text("<html/>", encoding="utf-8")
    (repo / "nested").mkdir()
    (repo / "nested" / "page.html").write_text("<html/>", encoding="utf-8")

    paths = update_executor._snapshot_candidates(  # noqa: SLF001
        ["assets/partials/header.html"], repo, "mobile-ux"
    )

    assert "assets/partials/header.html" in paths
    assert "index.html" in paths
    assert "nested/page.html" in paths


def test_post_patch_sync_runs_partial_injector(tmp_path: Path) -> None:
    """The partial-sync helper must run the injector, not the validate-only command."""
    with patch("repo_mgmt.update_executor.validation_runner.run_commands") as run_commands:
        run_commands.return_value = ValidationResult(
            True, ["python3 scripts/inject_partials.py"], "synced"
        )
        result = update_executor._run_post_patch_sync(tmp_path)  # noqa: SLF001

    run_commands.assert_called_once_with(
        ["python3 scripts/inject_partials.py"], cwd=tmp_path
    )
    assert result["passed"] is True
    assert result["commands"] == ["python3 scripts/inject_partials.py"]
