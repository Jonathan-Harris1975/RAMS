"""Safe Git manager for RAMS live-mode writes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .process_runner import run_bounded

_PROTECTED_BRANCHES = frozenset({"main", "master"})
_DEFAULT_GIT_TIMEOUT_SECONDS = 30
_DEFAULT_GIT_OUTPUT_MAX_BYTES = 65_536


class BranchSafetyError(Exception):
    """Raised when a Git operation would write to a protected branch."""


class GitManagerError(Exception):
    """Raised when an underlying git command fails."""


@dataclass(frozen=True)
class FileSnapshot:
    """Original bytes for one repo-relative file before a task starts."""

    path: str
    existed: bool
    content: bytes | None


@dataclass(frozen=True)
class TaskRepoSnapshot:
    """Exact Git state required to roll back one RAMS task."""

    branch: str
    head: str
    files: tuple[FileSnapshot, ...]


class GitManager:
    """Small Git wrapper that keeps RAMS writes branch-safe and path-scoped."""

    def __init__(
        self,
        target_repo: Path,
        branch_prefix: str = "rms-qa/",
        push_enabled: bool = False,
        timeout_seconds: int = _DEFAULT_GIT_TIMEOUT_SECONDS,
        max_output_bytes: int = _DEFAULT_GIT_OUTPUT_MAX_BYTES,
    ) -> None:
        """Initialise a branch-safe Git wrapper for one target repository."""
        self.target_repo = Path(target_repo)
        self.branch_prefix = branch_prefix
        self.push_enabled = push_enabled
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    def _git(self, *args: str) -> str:
        """Run Git with a bounded output tail and prompts disabled."""
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        result = run_bounded(
            ["git", *args],
            cwd=self.target_repo,
            timeout_seconds=self.timeout_seconds,
            max_output_bytes=self.max_output_bytes,
            max_output_lines=1_000,
            env=env,
            output_label="git",
        )
        if result.timed_out:
            raise GitManagerError(
                f"git {' '.join(args)} timed out after {self.timeout_seconds}s"
            )
        if result.return_code != 0:
            raise GitManagerError(
                result.output.strip() or f"git {' '.join(args)} failed"
            )
        return result.output.strip()

    def _git_no_output(self, *args: str) -> None:
        """Run git and discard stdout."""
        self._git(*args)

    def is_git_repo(self) -> bool:
        """Return True when target_repo is a usable Git work tree."""
        try:
            return self._git("rev-parse", "--is-inside-work-tree") == "true"
        except GitManagerError:
            return False

    def current_branch(self) -> str:
        """Return the active branch name."""
        return self._git("rev-parse", "--abbrev-ref", "HEAD")

    def current_head(self) -> str:
        """Return the current HEAD revision."""
        return self._git("rev-parse", "HEAD")

    def assert_write_allowed(self) -> None:
        """Block every file mutation, stage, commit, and push on main/master."""
        branch = self.current_branch()
        if branch in _PROTECTED_BRANCHES:
            raise BranchSafetyError(
                f"Refusing live write while active branch is protected: {branch!r}"
            )

    def status_porcelain(self) -> list[str]:
        """Return git status porcelain rows, including untracked files."""
        output = self._git("status", "--porcelain=v1", "--untracked-files=all")
        return [line for line in output.splitlines() if line]

    def is_worktree_clean(self) -> bool:
        """Return True when no tracked or untracked paths are dirty."""
        return not self.status_porcelain()

    def assert_clean_worktree(self) -> None:
        """Require a clean working tree before live writes begin."""
        dirty = self.status_porcelain()
        if dirty:
            sample = "; ".join(dirty[:10])
            raise GitManagerError(
                "Refusing live write because target repo has dirty or untracked files: "
                f"{sample}"
            )

    def make_qa_branch_name(self, pipeline_id: str, run_id: str) -> str:
        """Return the exact QA branch name for a pipeline run."""
        return f"{self.branch_prefix}{pipeline_id}/{run_id}"

    def create_branch(self, branch_name: str) -> str:
        """
        Create or check out a QA branch before any file mutation.

        Creating an rms-qa/* branch from main/master is intentionally permitted;
        all actual writes still call assert_write_allowed() after checkout.
        """
        if branch_name in _PROTECTED_BRANCHES:
            raise BranchSafetyError(
                f"Refusing to create/use protected branch {branch_name!r}"
            )
        if not branch_name.startswith(self.branch_prefix):
            raise BranchSafetyError(
                f"Refusing to create/use non-QA branch {branch_name!r}; "
                f"expected prefix {self.branch_prefix!r}"
            )
        self.assert_clean_worktree()
        branches = set(self._git("branch", "--format=%(refname:short)").splitlines())
        if branch_name in branches:
            self._git_no_output("checkout", branch_name)
        else:
            self._git_no_output("checkout", "-b", branch_name)
        self.assert_write_allowed()
        return branch_name

    def stage_task_files(self, paths: list[str]) -> None:
        """Stage only the exact paths modified by one task."""
        self.assert_write_allowed()
        if paths:
            self._git_no_output("add", "--", *paths)

    def commit(self, message: str) -> str:
        """Commit staged task files and return the short SHA."""
        self.assert_write_allowed()
        self._git_no_output("commit", "-m", message)
        return self._git("rev-parse", "--short", "HEAD")

    def push_branch(self, branch_name: str) -> None:
        """Push the current QA branch only when push is explicitly enabled."""
        if not self.push_enabled:
            return
        self.assert_write_allowed()
        if branch_name in _PROTECTED_BRANCHES or not branch_name.startswith(
            self.branch_prefix
        ):
            raise BranchSafetyError(
                f"Refusing to push non-QA/protected branch {branch_name!r}"
            )
        self._git_no_output("push", "origin", branch_name)

    def capture_task_state(self, paths: list[str]) -> TaskRepoSnapshot:
        """Capture branch, HEAD, and original bytes for task-touched paths."""
        normalised = tuple(dict.fromkeys(paths))
        root = self.target_repo.resolve()
        files: list[FileSnapshot] = []
        for rel in normalised:
            abs_path = (root / rel).resolve()
            try:
                abs_path.relative_to(root)
            except ValueError as exc:
                raise GitManagerError(
                    f"Refusing to snapshot path outside repo: {rel!r}"
                ) from exc
            if abs_path.exists():
                files.append(FileSnapshot(rel, True, abs_path.read_bytes()))
            else:
                files.append(FileSnapshot(rel, False, None))
        return TaskRepoSnapshot(
            branch=self.current_branch(),
            head=self.current_head(),
            files=tuple(files),
        )

    def restore_task_state(self, snapshot: TaskRepoSnapshot) -> None:
        """
        Restore only the files touched by a task, plus the task commit if created.

        No global git clean is used, so unrelated untracked files are preserved.
        """
        self.assert_write_allowed()
        current_head = self.current_head()
        if current_head != snapshot.head:
            self._git_no_output("reset", "--hard", snapshot.head)

        root = self.target_repo.resolve()
        paths = [file.path for file in snapshot.files]
        if paths:
            try:
                self._git_no_output("restore", "--staged", "--", *paths)
            except GitManagerError:
                # Some paths may be untracked or absent from the index; content
                # restoration below is still the source of truth.
                pass

        for file in snapshot.files:
            abs_path = (root / file.path).resolve()
            try:
                abs_path.relative_to(root)
            except ValueError as exc:
                raise GitManagerError(
                    f"Refusing to restore path outside repo: {file.path!r}"
                ) from exc
            if file.existed:
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_bytes(file.content or b"")
            elif abs_path.exists():
                if abs_path.is_dir():
                    raise GitManagerError(
                        f"Refusing to remove directory created at task path {file.path!r}"
                    )
                abs_path.unlink()

        if paths:
            try:
                self._git_no_output("restore", "--staged", "--", *paths)
            except GitManagerError:
                pass

    def revert(self) -> None:
        """Deprecated broad revert kept for compatibility; avoid for live tasks."""
        self.assert_write_allowed()
        self._git_no_output("reset", "--hard", "HEAD")
