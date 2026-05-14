"""Disposable local safety drill for RAMS live-write primitives.

This script does not touch any real target repository. It creates a temporary Git
repo, proves QA branch creation, exact-path staging, protected main/master write
refusal, and task-scoped rollback behaviour. It is intended as a local gate
before any real disposable-clone live-write exercise.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repo_mgmt.git_manager import BranchSafetyError, GitManager  # noqa: E402


def _run(cmd: list[str], cwd: Path) -> str:
    """Run a command inside cwd and return stdout."""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(repo: Path) -> None:
    """Create a disposable repo with two tracked files."""
    _run(["git", "init", "-b", "main"], repo)
    _run(["git", "config", "user.email", "rams-test@example.com"], repo)
    _run(["git", "config", "user.name", "RAMS Safety Drill"], repo)
    (repo / "index.html").write_text("<title>Old</title>\n", encoding="utf-8")
    (repo / "untouched.html").write_text("keep\n", encoding="utf-8")
    _run(["git", "add", "index.html", "untouched.html"], repo)
    _run(["git", "commit", "-m", "init"], repo)


def main() -> None:
    """Run the disposable branch-safety check."""
    with tempfile.TemporaryDirectory(prefix="rams-live-branch-") as tmp:
        repo = Path(tmp)
        _init_repo(repo)
        gm = GitManager(repo)

        (repo / "index.html").write_text("<title>Dirty</title>\n", encoding="utf-8")
        try:
            gm.stage_task_files(["index.html"])
        except BranchSafetyError:
            pass
        else:
            raise AssertionError("stage_task_files must refuse writes on main")
        _run(["git", "checkout", "--", "index.html"], repo)

        branch = gm.create_branch(gm.make_qa_branch_name("on-brand", "script-run"))
        assert branch == "rms-qa/on-brand/script-run"
        assert gm.current_branch() == branch

        snapshot = gm.capture_task_state(["index.html"])
        (repo / "index.html").write_text("<title>New</title>\n", encoding="utf-8")
        (repo / "operator-notes.txt").write_text("must remain unstaged\n", encoding="utf-8")
        gm.stage_task_files(["index.html"])
        staged = _run(["git", "diff", "--cached", "--name-only"], repo).splitlines()
        assert staged == ["index.html"], staged

        gm.restore_task_state(snapshot)
        assert (repo / "index.html").read_text(encoding="utf-8") == "<title>Old</title>\n"
        assert (repo / "operator-notes.txt").read_text(encoding="utf-8") == "must remain unstaged\n"
        assert "operator-notes.txt" in "\n".join(gm.status_porcelain())
        print("Disposable live-branch safety check passed.")


if __name__ == "__main__":
    main()
