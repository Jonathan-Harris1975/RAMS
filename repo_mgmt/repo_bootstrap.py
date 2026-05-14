"""Runtime repository bootstrap for Koyeb-style ephemeral containers.

RAMS needs local working copies of the two controlled target repositories before
pipeline admission can safely proceed. This module optionally clones or refreshes
those repositories from explicit environment-configured Git URLs. It never logs
secrets and it never guesses repository names.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from repo_mgmt.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BootstrapTarget:
    """A repository that may be cloned/refreshed for runtime use."""

    label: str
    url: str
    branch: str
    path: Path


@dataclass(frozen=True)
class BootstrapResult:
    """Public, non-secret result for a single bootstrap target."""

    label: str
    path: str
    attempted: bool
    ready: bool
    action: str
    error: str | None = None


def _git_base_command(token: str | None) -> list[str]:
    """Return a git command prefix using a non-logged auth header when supplied."""
    command = ["git"]
    if token and token.strip():
        command.extend(
            [
                "-c",
                f"http.extraHeader=AUTHORIZATION: bearer {token.strip()}",
            ]
        )
    return command


def _safe_command_for_log(command: list[str], token: str | None) -> str:
    """Return a log-safe command string with any token redacted."""
    rendered = " ".join(command)
    if token and token.strip():
        rendered = rendered.replace(token.strip(), "<redacted>")
    return rendered


def _run(command: list[str], *, cwd: Path | None, token: str | None) -> None:
    """Run a git command and raise a scrubbed RuntimeError on failure."""
    logger.debug("repo_bootstrap: running %s", _safe_command_for_log(command, token))
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=180,
    )
    if result.returncode != 0:
        output = result.stdout.strip()
        if token and token.strip():
            output = output.replace(token.strip(), "<redacted>")
        raise RuntimeError(output or f"git exited with {result.returncode}")


def _is_git_worktree(path: Path) -> bool:
    """Return True when *path* appears to be a Git worktree."""
    return path.is_dir() and (path / ".git").exists()


def _clone_or_refresh(target: BootstrapTarget, token: str | None) -> BootstrapResult:
    """Clone a missing repo or refresh an existing worktree to the configured branch."""
    if not target.url.strip():
        return BootstrapResult(
            label=target.label,
            path=str(target.path),
            attempted=False,
            ready=_is_git_worktree(target.path),
            action="skipped_missing_url",
            error="repo URL is not configured",
        )

    try:
        target.path.parent.mkdir(parents=True, exist_ok=True)
        git = _git_base_command(token)
        if _is_git_worktree(target.path):
            _run([*git, "fetch", "--depth", "1", "origin", target.branch], cwd=target.path, token=token)
            _run([*git, "checkout", "-B", target.branch, "FETCH_HEAD"], cwd=target.path, token=token)
            return BootstrapResult(
                label=target.label,
                path=str(target.path),
                attempted=True,
                ready=True,
                action="refreshed",
            )

        if target.path.exists() and any(target.path.iterdir()):
            shutil.rmtree(target.path)

        _run(
            [
                *git,
                "clone",
                "--depth",
                "1",
                "--branch",
                target.branch,
                target.url,
                str(target.path),
            ],
            cwd=None,
            token=token,
        )
        return BootstrapResult(
            label=target.label,
            path=str(target.path),
            attempted=True,
            ready=_is_git_worktree(target.path),
            action="cloned",
        )
    except Exception as exc:
        logger.warning(
            "repo_bootstrap: %s bootstrap failed for %s: %s",
            target.label,
            target.path,
            exc,
        )
        return BootstrapResult(
            label=target.label,
            path=str(target.path),
            attempted=True,
            ready=False,
            action="failed",
            error=str(exc),
        )


def targets_from_settings(cfg: Settings) -> list[BootstrapTarget]:
    """Build the configured website and AIMS bootstrap targets."""
    return [
        BootstrapTarget(
            label="website",
            url=cfg.rms_website_repo_url,
            branch=cfg.rms_website_repo_branch,
            path=cfg.repo_path_for("seo-aeo-geo"),
        ),
        BootstrapTarget(
            label="aims",
            url=cfg.rms_aims_repo_url,
            branch=cfg.rms_aims_repo_branch,
            path=cfg.repo_path_for("on-brand"),
        ),
    ]


def bootstrap_repositories(cfg: Settings) -> list[BootstrapResult]:
    """Clone or refresh configured target repositories when explicitly enabled."""
    if not cfg.rms_repo_bootstrap_enabled:
        return [
            BootstrapResult(
                label=target.label,
                path=str(target.path),
                attempted=False,
                ready=_is_git_worktree(target.path) or target.path.is_dir(),
                action="disabled",
            )
            for target in targets_from_settings(cfg)
        ]

    results = [
        _clone_or_refresh(target, cfg.github_token)
        for target in targets_from_settings(cfg)
    ]
    logger.info(
        "repo_bootstrap: completed results=%s",
        [result.__dict__ for result in results],
    )
    return results
