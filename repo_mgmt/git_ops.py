"""Deprecated compatibility Git helpers.

Canonical live-write Git safety now lives in repo_mgmt.git_manager. This module
is retained only for legacy callers and tests; new production code must not use
its broad revert helper.
"""

from __future__ import annotations
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

_PROTECTED_BRANCHES = frozenset(["main", "master"])


class BranchSafetyError(Exception):
    pass


class GitOpsError(Exception):
    pass


class _CliGit:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _run(self, *args: str) -> str:
        p = subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if p.returncode != 0:
            raise GitOpsError(
                p.stderr.strip() or p.stdout.strip() or f"git {' '.join(args)} failed"
            )
        return p.stdout.strip()

    def push(self, *args: str) -> str:
        return self._run("push", *args)

    def reset(self, *args: str) -> str:
        return self._run("reset", *args)

    def clean(self, *args: str) -> str:
        return self._run("clean", *args)

    def checkout(self, *args: str) -> str:
        return self._run("checkout", *args)


class _CliIndex:
    def __init__(self, root: Path):
        self.root = Path(root)

    def add(self, paths: list[str]) -> None:
        if paths:
            subprocess.run(
                ["git", "add", "--", *paths],
                cwd=self.root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

    def commit(self, message: str) -> SimpleNamespace:
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=self.root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.root,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        return SimpleNamespace(hexsha=sha)


class _CliRepo:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.git = _CliGit(root)
        self.index = _CliIndex(root)

    @property
    def active_branch(self) -> SimpleNamespace:
        p = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=self.root,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        return SimpleNamespace(name=p.stdout.strip())

    @property
    def branches(self) -> list[SimpleNamespace]:
        p = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            cwd=self.root,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        return [SimpleNamespace(name=x) for x in p.stdout.splitlines() if x]


def ensure_clean_branch(repo_root: Path, branch_name: str) -> _CliRepo:
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
    names = [b.name for b in repo.branches]
    if branch_name in names:
        repo.git.checkout(branch_name)
    else:
        repo.git.checkout("-b", branch_name)
    return repo


def stage_and_commit(
    repo: Any, paths: list[str], message: str, dry_run: bool = True
) -> str | None:
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
    if branch_name in _PROTECTED_BRANCHES:
        raise BranchSafetyError(
            f"Refusing to push to protected branch {branch_name!r}."
        )
    if dry_run or not push_enabled:
        return
    repo.git.push("origin", branch_name)


def revert_to_head(repo: Any, dry_run: bool = True) -> None:
    if dry_run:
        return
    repo.git.reset("--hard", "HEAD")
    repo.git.clean("-fd")
