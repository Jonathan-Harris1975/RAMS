"""Runtime repository bootstrap for Koyeb-style ephemeral containers.

RAMS needs local working copies of the controlled target repositories before a
pipeline can safely proceed. This module optionally clones or refreshes those
repositories from explicit environment-configured Git URLs. It avoids logging
secrets, disables interactive Git credential prompts, and provides operator-safe
errors when GitHub authentication is missing or rejected.
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from repo_mgmt.config import PipelineId, Settings
from repo_mgmt.process_runner import run_bounded

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


def _token_markers(token: str | None) -> list[str]:
    """Return raw/encoded token markers that must be redacted from logs."""
    if not token or not token.strip():
        return []
    raw = token.strip()
    basic = base64.b64encode(f"x-access-token:{raw}".encode("utf-8")).decode("ascii")
    return [raw, basic]


def _is_placeholder_secret(value: str | None) -> bool:
    """Return True when a secret reference was passed literally instead of resolved."""
    if not value:
        return False
    stripped = value.strip()
    return stripped.startswith("{{secret.") or stripped.startswith("${{")


def _is_github_https_url(url: str) -> bool:
    """Return True when *url* is a GitHub HTTPS repository URL."""
    parsed = urlparse(url.strip())
    return parsed.scheme == "https" and parsed.hostname == "github.com"


def _git_base_command(token: str | None, url: str) -> list[str]:
    """Return a git command prefix with GitHub HTTPS auth when supplied.

    GitHub's Git-over-HTTPS endpoint is most reliable with an HTTP Basic auth
    header using the special ``x-access-token`` username. The header is passed
    through git config rather than embedding credentials into the remote URL, so
    the token does not become part of ``origin`` or normal process output.
    """
    command = ["git"]
    if token and token.strip() and _is_github_https_url(url):
        encoded = base64.b64encode(
            f"x-access-token:{token.strip()}".encode("utf-8")
        ).decode("ascii")
        command.extend(
            [
                "-c",
                f"http.https://github.com/.extraheader=Authorization: Basic {encoded}",
            ]
        )
    return command


def _safe_command_for_log(command: list[str], token: str | None) -> str:
    """Return a log-safe command string with any token material redacted."""
    rendered = " ".join(command)
    for marker in _token_markers(token):
        rendered = rendered.replace(marker, "<redacted>")
    return rendered


def _scrub_output(output: str, token: str | None) -> str:
    """Remove secret material from Git output before it reaches logs/API JSON."""
    scrubbed = output
    for marker in _token_markers(token):
        scrubbed = scrubbed.replace(marker, "<redacted>")
    return scrubbed


def _operator_hint(output: str, *, url: str, token: str | None) -> str:
    """Append a clear non-secret hint for common GitHub bootstrap failures."""
    lower = output.lower()
    hints: list[str] = []
    if _is_github_https_url(url):
        if not token or not token.strip():
            hints.append(
                "GitHub token is not available. Set RMS_GITHUB_TOKEN or "
                "GITHUB_TOKEN in Koyeb with read access to the target repos."
            )
        elif _is_placeholder_secret(token):
            hints.append(
                "GitHub token still looks like an unresolved secret reference; "
                "check the Koyeb secret name and service env binding."
            )
        if (
            "could not read username" in lower
            or "authentication failed" in lower
            or "repository not found" in lower
            or "403" in lower
            or "401" in lower
        ):
            hints.append(
                "For private GitHub repos, use a fine-grained token with Contents: "
                "Read access for both the website and AIMS repositories."
            )
    if not hints:
        return output
    return f"{output}\nOperator hint: " + " ".join(dict.fromkeys(hints))


def _run(
    command: list[str],
    *,
    cwd: Path | None,
    token: str | None,
    url: str,
    timeout_seconds: int,
    max_output_bytes: int,
) -> None:
    """Run a Git command and raise a scrubbed RuntimeError on failure."""
    logger.debug("repo_bootstrap: running %s", _safe_command_for_log(command, token))
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_ASKPASS", "true")
    result = run_bounded(
        command,
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        max_output_lines=200,
        output_label="git",
    )
    if result.return_code != 0:
        output = _scrub_output(result.output.strip(), token)
        output = _operator_hint(
            output or f"git exited with {result.return_code}", url=url, token=token
        )
        raise RuntimeError(output)


def _is_git_worktree(path: Path) -> bool:
    """Return True when *path* appears to be a Git worktree."""
    return path.is_dir() and (path / ".git").exists()


def _clone_or_refresh(
    target: BootstrapTarget,
    token: str | None,
    *,
    timeout_seconds: int,
    clone_depth: int,
    max_output_bytes: int,
) -> BootstrapResult:
    """Clone a missing repo or refresh an existing worktree to the configured branch."""
    url = target.url.strip()
    if not url:
        return BootstrapResult(
            label=target.label,
            path=str(target.path),
            attempted=False,
            ready=_is_git_worktree(target.path),
            action="skipped_missing_url",
            error="repo URL is not configured",
        )

    if _is_placeholder_secret(url):
        return BootstrapResult(
            label=target.label,
            path=str(target.path),
            attempted=False,
            ready=False,
            action="failed",
            error=(
                "repo URL is an unresolved Koyeb secret reference; check "
                f"RMS_{target.label.upper()}_REPO_URL secret binding"
            ),
        )

    if _is_placeholder_secret(token):
        return BootstrapResult(
            label=target.label,
            path=str(target.path),
            attempted=False,
            ready=False,
            action="failed",
            error=(
                "GitHub token is an unresolved Koyeb secret reference; check "
                "RMS_GITHUB_TOKEN/GITHUB_TOKEN secret binding"
            ),
        )

    try:
        target.path.parent.mkdir(parents=True, exist_ok=True)
        git = _git_base_command(token, url)
        if _is_git_worktree(target.path):
            _run(
                [
                    *git,
                    "fetch",
                    "--no-tags",
                    "--depth",
                    str(clone_depth),
                    "origin",
                    target.branch,
                ],
                cwd=target.path,
                token=token,
                url=url,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
            )
            _run(
                [*git, "checkout", "-B", target.branch, "FETCH_HEAD"],
                cwd=target.path,
                token=token,
                url=url,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
            )
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
                str(clone_depth),
                "--no-tags",
                "--single-branch",
                "--branch",
                target.branch,
                url,
                str(target.path),
            ],
            cwd=None,
            token=token,
            url=url,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
        return BootstrapResult(
            label=target.label,
            path=str(target.path),
            attempted=True,
            ready=_is_git_worktree(target.path),
            action="cloned",
        )
    except Exception as exc:
        if target.path.exists() and not _is_git_worktree(target.path):
            shutil.rmtree(target.path, ignore_errors=True)
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
            path=cfg.repo_path_for("website"),
        ),
        BootstrapTarget(
            label="aims",
            url=cfg.rms_aims_repo_url,
            branch=cfg.rms_aims_repo_branch,
            path=cfg.repo_path_for("on-brand"),
        ),
    ]


def targets_for_pipeline(
    cfg: Settings, pipeline_id: PipelineId
) -> list[BootstrapTarget]:
    """Return only the bootstrap target required by *pipeline_id*."""
    if pipeline_id in {"website", "seo-aeo-geo", "mobile-ux"}:
        return [
            BootstrapTarget(
                label="website",
                url=cfg.rms_website_repo_url,
                branch=cfg.rms_website_repo_branch,
                path=cfg.repo_path_for(pipeline_id),
            )
        ]
    if pipeline_id == "on-brand":
        return [
            BootstrapTarget(
                label="aims",
                url=cfg.rms_aims_repo_url,
                branch=cfg.rms_aims_repo_branch,
                path=cfg.repo_path_for("on-brand"),
            )
        ]
    return []


def bootstrap_repositories(
    cfg: Settings,
    pipeline_id: PipelineId | None = None,
) -> list[BootstrapResult]:
    """Clone or refresh configured target repositories when explicitly enabled."""
    targets = (
        targets_for_pipeline(cfg, pipeline_id)
        if pipeline_id
        else targets_from_settings(cfg)
    )
    if not cfg.rms_repo_bootstrap_enabled:
        return [
            BootstrapResult(
                label=target.label,
                path=str(target.path),
                attempted=False,
                ready=_is_git_worktree(target.path) or target.path.is_dir(),
                action="disabled",
            )
            for target in targets
        ]

    results = [
        _clone_or_refresh(
            target,
            cfg.github_token_value,
            timeout_seconds=cfg.rms_git_timeout_seconds,
            clone_depth=cfg.rms_git_clone_depth,
            max_output_bytes=cfg.rms_git_output_max_bytes,
        )
        for target in targets
    ]
    logger.info(
        "repo_bootstrap: completed results=%s",
        [result.__dict__ for result in results],
    )
    return results
