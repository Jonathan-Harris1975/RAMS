"""
Git manager for the Repo Management Suite.

Provides safe, audited git operations for a single repository path supplied
at construction time.  Never reads a global repo path from config.

Branch naming convention:
  <RMS_QA_BRANCH_PREFIX><pipeline_id>/<run_id>
  e.g.  rms-qa/on-brand/2026-05-05T03-00-00Z

Safety guarantees:
  - Raises BranchSafetyError before any write if the active branch is
    main or master.
  - stage_task_files stages only the exact paths supplied — never any
    unrelated dirty files.
  - push_branch only runs if RMS_PUSH_ENABLED=true.
  - Validation must pass before commit.
  - Failed validation triggers revert when RMS_REVERT_ON_VALIDATION_FAILURE=true.
"""

from __future__ import annotations

import logging
from pathlib import Path

import git

logger = logging.getLogger(__name__)

_PROTECTED_BRANCHES = frozenset(["main", "master"])


class BranchSafetyError(Exception):
    """Raised when a write is attempted directly on main or master."""


class GitManagerError(Exception):
    """Raised when a git operation fails for a non-safety reason."""


class GitManager:
    """Manages git operations for a single repository clone."""

    def __init__(
        self,
        target_repo: Path,
        branch_prefix: str = "rms-qa/",
        push_enabled: bool = False,
        revert_on_failure: bool = True,
    ) -> None:
        """
        Initialise a GitManager for *target_repo*.

        Args:
            target_repo: Absolute path to the repository root.
            branch_prefix: Prefix prepended to all RMS QA branches.
            push_enabled: If True, push_branch() will push to origin.
            revert_on_failure: If True, revert() hard-resets to HEAD on failure.
        """
        self._repo_path = target_repo
        self._branch_prefix = branch_prefix
        self._push_enabled = push_enabled
        self._revert_on_failure = revert_on_failure
        self._repo: git.Repo | None = None

    # ── Internal helpers ───────────────────────────────────────────────────

    def _open(self) -> git.Repo:
        """Open (or return cached) git.Repo for the target repo."""
        if self._repo is None:
            try:
                self._repo = git.Repo(self._repo_path)
            except git.InvalidGitRepositoryError as exc:
                raise GitManagerError(
                    f"Not a git repository: {self._repo_path}"
                ) from exc
        return self._repo

    def _check_not_protected(self) -> None:
        """Raise BranchSafetyError if the active branch is main or master."""
        repo = self._open()
        active = repo.active_branch.name
        if active in _PROTECTED_BRANCHES:
            raise BranchSafetyError(
                f"Active branch is {active!r}. RMS refuses to write directly to "
                f"a protected branch. Create a feature branch first."
            )

    # ── Public API ─────────────────────────────────────────────────────────

    def branch_name(self, pipeline_id: str, run_id: str) -> str:
        """
        Build the RMS QA branch name for a given pipeline run.

        Args:
            pipeline_id: e.g. 'on-brand'.
            run_id: ISO-UTC run identifier, e.g. '2026-05-05T03-00-00Z'.

        Returns:
            Full branch name string.
        """
        return f"{self._branch_prefix}{pipeline_id}/{run_id}"

    def create_branch(self, pipeline_id: str, run_id: str) -> str:
        """
        Create and check out a new RMS QA branch.

        Args:
            pipeline_id: Pipeline identifier.
            run_id: Run identifier string.

        Returns:
            The new branch name.

        Raises:
            BranchSafetyError: If the current branch is main/master.
            GitManagerError: If branch creation fails.
        """
        repo = self._open()
        name = self.branch_name(pipeline_id, run_id)
        try:
            new_branch = repo.create_head(name)
            new_branch.checkout()
            logger.info("git_manager: created and checked out branch %r", name)
        except git.GitCommandError as exc:
            raise GitManagerError(f"Failed to create branch {name!r}: {exc}") from exc
        return name

    def stage_task_files(self, paths: list[str]) -> None:
        """
        Stage exactly the supplied *paths* — no unrelated dirty files.

        Args:
            paths: Repo-relative path strings to add to the index.

        Raises:
            BranchSafetyError: If on a protected branch.
            GitManagerError: If staging fails.
        """
        self._check_not_protected()
        repo = self._open()
        try:
            repo.index.add(paths)
            logger.info("git_manager: staged %d file(s): %s", len(paths), paths)
        except git.GitCommandError as exc:
            raise GitManagerError(f"Failed to stage files: {exc}") from exc

    def commit(self, message: str) -> str:
        """
        Commit the current index with *message*.

        Args:
            message: Commit message string.

        Returns:
            Hex SHA of the new commit.

        Raises:
            BranchSafetyError: If on a protected branch.
            GitManagerError: If committing fails.
        """
        self._check_not_protected()
        repo = self._open()
        try:
            commit = repo.index.commit(message)
            sha = commit.hexsha[:12]
            logger.info("git_manager: committed %s — %r", sha, message)
            return sha
        except git.GitCommandError as exc:
            raise GitManagerError(f"Failed to commit: {exc}") from exc

    def push_branch(self, branch_name: str) -> None:
        """
        Push *branch_name* to origin if push_enabled is True.

        Args:
            branch_name: Branch to push.
        """
        if not self._push_enabled:
            logger.info("git_manager: push disabled — skipping push of %r", branch_name)
            return
        repo = self._open()
        try:
            repo.remotes.origin.push(branch_name)
            logger.info("git_manager: pushed %r to origin", branch_name)
        except git.GitCommandError as exc:
            raise GitManagerError(f"Failed to push {branch_name!r}: {exc}") from exc

    def revert(self) -> None:
        """
        Hard-reset the working tree to HEAD, discarding all staged and unstaged changes.

        Only executed when revert_on_failure is True.
        """
        if not self._revert_on_failure:
            logger.info("git_manager: revert_on_failure=False — not reverting")
            return
        repo = self._open()
        try:
            repo.git.reset("--hard", "HEAD")
            repo.git.clean("-fd")
            logger.info("git_manager: reverted to HEAD")
        except git.GitCommandError as exc:
            raise GitManagerError(f"Failed to revert: {exc}") from exc
