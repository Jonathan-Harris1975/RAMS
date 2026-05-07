"""
Git operations for the Repo Management Suite.

Wraps GitPython for all git interactions:
  - Branch creation / checkout (raises BranchSafetyError if on main/master)
  - Staging and committing modified files
  - Optional push to origin
  - Revert (hard-reset to HEAD) on validation failure

Never pushes or creates PRs unless explicitly enabled in config.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import git

if TYPE_CHECKING:
    from repo_mgmt.config import Settings

logger = logging.getLogger(__name__)

_PROTECTED_BRANCHES = frozenset(["main", "master"])


class BranchSafetyError(Exception):
    """
    Raised when the active branch is a protected branch (main/master) and
    the caller attempts to commit without first creating a feature branch.
    """


class GitOpsError(Exception):
    """Raised when a git operation fails for non-safety reasons."""


def ensure_clean_branch(
    repo_root: Path,
    branch_name: str,
) -> git.Repo:
    """
    Ensure the repo is on *branch_name*, creating it from HEAD if needed.

    Args:
        repo_root: Absolute path to the local repository clone.
        branch_name: Desired branch name (must not be main/master).

    Returns:
        The open git.Repo object.

    Raises:
        BranchSafetyError: If *branch_name* is main or master.
        GitOpsError: If the GitPython operation fails.
    """
    if branch_name in _PROTECTED_BRANCHES:
        raise BranchSafetyError(
            f"Refusing to work directly on protected branch {branch_name!r}. "
            "Set RMS_QA_BRANCH_PREFIX to use a feature branch."
        )

    try:
        repo = git.Repo(repo_root)
        current = repo.active_branch.name
        if current == branch_name:
            logger.info("git_ops: already on branch %r", branch_name)
            return repo

        if branch_name in [b.name for b in repo.branches]:
            repo.git.checkout(branch_name)
            logger.info("git_ops: checked out existing branch %r", branch_name)
        else:
            repo.git.checkout("-b", branch_name)
            logger.info("git_ops: created and checked out new branch %r", branch_name)

        return repo
    except git.GitCommandError as exc:
        raise GitOpsError(f"Failed to checkout branch {branch_name!r}: {exc}") from exc


def stage_and_commit(
    repo: git.Repo,
    paths: list[str],
    message: str,
    dry_run: bool = True,
) -> str | None:
    """
    Stage *paths* and create a commit with *message*.

    Args:
        repo: Open git.Repo instance (from ensure_clean_branch).
        paths: List of relative paths to stage.
        message: Commit message.
        dry_run: If True, log what would happen but make no git changes.

    Returns:
        Short commit SHA on success, None in dry-run mode.

    Raises:
        BranchSafetyError: If the active branch is main or master.
        GitOpsError: If the git operation fails.
    """
    branch = repo.active_branch.name
    if branch in _PROTECTED_BRANCHES:
        raise BranchSafetyError(
            f"Refusing to commit directly to protected branch {branch!r}."
        )

    if dry_run:
        logger.info(
            "git_ops [dry-run]: would stage %d file(s) and commit: %r",
            len(paths),
            message,
        )
        return None

    try:
        repo.index.add(paths)
        commit = repo.index.commit(message)
        short_sha = commit.hexsha[:8]
        logger.info("git_ops: committed %d file(s) as %s — %r", len(paths), short_sha, message)
        return short_sha
    except git.GitCommandError as exc:
        raise GitOpsError(f"Commit failed: {exc}") from exc


def push_branch(
    repo: git.Repo,
    branch_name: str,
    dry_run: bool = True,
    push_enabled: bool = False,
) -> None:
    """
    Push *branch_name* to origin.

    Args:
        repo: Open git.Repo instance.
        branch_name: Branch to push.
        dry_run: If True, skip push regardless of push_enabled.
        push_enabled: Must be True (and dry_run False) to actually push.

    Raises:
        BranchSafetyError: If *branch_name* is main or master.
        GitOpsError: If the push fails.
    """
    if branch_name in _PROTECTED_BRANCHES:
        raise BranchSafetyError(
            f"Refusing to push to protected branch {branch_name!r}."
        )

    if dry_run or not push_enabled:
        logger.info(
            "git_ops: push skipped (dry_run=%s push_enabled=%s)",
            dry_run,
            push_enabled,
        )
        return

    try:
        repo.git.push("origin", branch_name)
        logger.info("git_ops: pushed branch %r to origin", branch_name)
    except git.GitCommandError as exc:
        raise GitOpsError(f"Push failed for branch {branch_name!r}: {exc}") from exc


def revert_to_head(repo: git.Repo, dry_run: bool = True) -> None:
    """
    Hard-reset the working tree to HEAD, discarding all uncommitted changes.

    Args:
        repo: Open git.Repo instance.
        dry_run: If True, log what would happen but make no git changes.

    Raises:
        GitOpsError: If the reset fails.
    """
    if dry_run:
        logger.info("git_ops [dry-run]: would hard-reset to HEAD")
        return

    try:
        repo.git.reset("--hard", "HEAD")
        repo.git.clean("-fd")
        logger.info("git_ops: hard-reset to HEAD and cleaned untracked files")
    except git.GitCommandError as exc:
        raise GitOpsError(f"Hard-reset failed: {exc}") from exc
