"""Safe Git manager for RAMS live-mode writes."""

from __future__ import annotations
import subprocess
from pathlib import Path

_PROTECTED_BRANCHES = frozenset({"main", "master"})


class BranchSafetyError(Exception):
    pass


class GitManagerError(Exception):
    pass


class GitManager:
    def __init__(
        self,
        target_repo: Path,
        branch_prefix: str = "rms-qa/",
        push_enabled: bool = False,
    ) -> None:
        self.target_repo = Path(target_repo)
        self.branch_prefix = branch_prefix
        self.push_enabled = push_enabled

    def _git(self, *args: str) -> str:
        p = subprocess.run(
            ["git", *args],
            cwd=self.target_repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if p.returncode != 0:
            raise GitManagerError(
                p.stderr.strip() or p.stdout.strip() or f"git {' '.join(args)} failed"
            )
        return p.stdout.strip()

    def current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD")

    def assert_write_allowed(self) -> None:
        b = self.current_branch()
        if b in _PROTECTED_BRANCHES:
            raise BranchSafetyError(
                f"Refusing live write while active branch is protected: {b!r}"
            )

    def create_branch(self, branch_name: str) -> str:
        if branch_name in _PROTECTED_BRANCHES:
            raise BranchSafetyError(
                f"Refusing to create/use protected branch {branch_name!r}"
            )
        self.assert_write_allowed()
        branches = set(self._git("branch", "--format=%(refname:short)").splitlines())
        self._git("checkout", branch_name) if branch_name in branches else self._git(
            "checkout", "-b", branch_name
        )
        return branch_name

    def stage_task_files(self, paths: list[str]) -> None:
        self.assert_write_allowed()
        if paths:
            self._git("add", "--", *paths)

    def commit(self, message: str) -> str:
        self.assert_write_allowed()
        self._git("commit", "-m", message)
        return self._git("rev-parse", "--short", "HEAD")

    def push_branch(self, branch_name: str) -> None:
        if not self.push_enabled:
            return
        self.assert_write_allowed()
        if branch_name in _PROTECTED_BRANCHES:
            raise BranchSafetyError(
                f"Refusing to push protected branch {branch_name!r}"
            )
        self._git("push", "origin", branch_name)

    def revert(self) -> None:
        self.assert_write_allowed()
        self._git("reset", "--hard", "HEAD")
        self._git("clean", "-fd")
