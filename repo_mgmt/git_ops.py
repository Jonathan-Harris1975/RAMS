"""Deprecated compatibility Git helpers.

Canonical live-write Git safety now lives in repo_mgmt.git_manager. This module
is retained only for legacy callers and tests; new production code must not use
its broad revert helper.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

_PROTECTED_BRANCHES = frozenset(["main", "master"])
_GIT_TIMEOUT_SECONDS = 30


class BranchSafetyError(Exception):
    """Raised when a legacy helper is asked to write to main/master."""


class GitOpsError(Exception):
    """Raised when a legacy Git subprocess fails or times out."""


def _git_env() -> dict[str, str]:
    """Return an environment that disables interactive Git credential prompts."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


class _CliGit:
    """Tiny subprocess-backed replacement for the old GitPython-style surface."""

    def __init__(self, root: Path) -> None:
        """Store the repository root used for all subprocess calls."""
        self.root = Path(root)

    def _run(self, *args: str) -> str:
        """Run a bounded Git subprocess and return stripped stdout."""
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=self.root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=_GIT_TIMEOUT_SECONDS,
                env=_git_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitOpsError(
                f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_SECONDS}s"
            ) from exc
        if proc.returncode != 0:
            raise GitOpsError(
                proc.stderr.strip()
                or proc.stdout.strip()
                or f"git {' '.join(args)} failed"
            )
        return proc.stdout.strip()

    def push(self, *args: str) -> str:
        """Run git push with bounded timeout handling."""
        return self._run("push", *args)

    def reset(self, *args: str) -> str:
        """Run git reset with bounded timeout handling."""
        return self._run("reset", *args)

    def clean(self, *args: str) -> str:
        """Run git clean with bounded timeout handling."""
        return self._run("clean", *args)

    def checkout(self, *args: str) -> str:
        """Run git checkout with bounded timeout handling."""
        return self._run("checkout", *args)


class _CliIndex:
    """Minimal index facade used by the deprecated compatibility helper."""

    def __init__(self, root: Path) -> None:
        """Store the repository root used for index operations."""
        self.root = Path(root)

    def add(self, paths: list[str]) -> None:
        """Stage the supplied paths using a bounded Git subprocess."""
        if not paths:
            return
        try:
            subprocess.run(
                ["git", "add", "--", *paths],
                cwd=self.root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_GIT_TIMEOUT_SECONDS,
                env=_git_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitOpsError(
                f"git add timed out after {_GIT_TIMEOUT_SECONDS}s"
            ) from exc

    def commit(self, message: str) -> SimpleNamespace:
        """Commit staged files and return an object exposing hexsha."""
        try:
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_GIT_TIMEOUT_SECONDS,
                env=_git_env(),
            )
            sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.root,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_GIT_TIMEOUT_SECONDS,
                env=_git_env(),
            ).stdout.strip()
        except subprocess.TimeoutExpired as exc:
            raise GitOpsError(
                f"git commit/rev-parse timed out after {_GIT_TIMEOUT_SECONDS}s"
            ) from exc
        return SimpleNamespace(hexsha=sha)


class _CliRepo:
    """Minimal repository facade retained for backwards-compatible tests."""

    def __init__(self, root: Path) -> None:
        """Initialise git and index facades for *root*."""
        self.root = Path(root)
        self.git = _CliGit(root)
        self.index = _CliIndex(root)

    @property
    def active_branch(self) -> SimpleNamespace:
        """Return an object exposing the active branch name."""
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.root,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_GIT_TIMEOUT_SECONDS,
                env=_git_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitOpsError(
                f"git rev-parse timed out after {_GIT_TIMEOUT_SECONDS}s"
            ) from exc
        return SimpleNamespace(name=proc.stdout.strip())

    @property
    def branches(self) -> list[SimpleNamespace]:
        """Return branch names as SimpleNamespace objects."""
        try:
            proc = subprocess.run(
                ["git", "branch", "--format=%(refname:short)"],
                cwd=self.root,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_GIT_TIMEOUT_SECONDS,
                env=_git_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitOpsError(
                f"git branch timed out after {_GIT_TIMEOUT_SECONDS}s"
            ) from exc
        return [SimpleNamespace(name=line) for line in proc.stdout.splitlines() if line]


def ensure_clean_branch(repo_root: Path, branch_name: str) -> _CliRepo:
    """Return a legacy repo facade after checking out a non-protected branch."""
    if branch_name in _PROTECTED_BRANCHES:
        raise BranchSafetyError(
            f"Refusing to work directly on protected branch {branch_name!r}."
        )
    repo = _CliRepo(repo_root)
    current = repo.active_branch.name
    if current in _PROTECTED_BRANCHES:
        raise BranchSafetyError(
            f"Refusing live write while active branch is protected: {current!r}."
        )
    if current == branch_name:
        return repo
    names = [branch.name for branch in repo.branches]
    if branch_name in names:
        repo.git.checkout(branch_name)
    else:
        repo.git.checkout("-b", branch_name)
    return repo


def stage_and_commit(
    repo: Any, paths: list[str], message: str, dry_run: bool = True
) -> str | None:
    """Stage exact paths and commit through the deprecated facade."""
    branch = repo.active_branch.name
    if branch in _PROTECTED_BRANCHES:
        raise BranchSafetyError(
            f"Refusing to commit directly to protected branch {branch!r}."
        )
    if dry_run:
        return None
    if paths:
        repo.index.add(paths)
    commit = repo.index.commit(message)
    return cast(str, commit.hexsha[:8])


def push_branch(
    repo: Any, branch_name: str, dry_run: bool = True, push_enabled: bool = False
) -> None:
    """Push a non-protected branch only when explicitly enabled."""
    if branch_name in _PROTECTED_BRANCHES:
        raise BranchSafetyError(
            f"Refusing to push to protected branch {branch_name!r}."
        )
    if dry_run or not push_enabled:
        return
    repo.git.push("origin", branch_name)


def revert_to_head(repo: Any, dry_run: bool = True) -> None:
    """Perform the legacy broad revert; retained only for compatibility."""
    if dry_run:
        return
    repo.git.reset("--hard", "HEAD")
    repo.git.clean("-fd")
