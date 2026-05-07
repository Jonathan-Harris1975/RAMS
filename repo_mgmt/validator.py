"""
Validator for the Repo Management Suite.

Runs the pipeline's validation commands in the repository working directory.
Returns a ValidationResult (passed/failed + output). Never raises on command
failure — captures stderr/stdout and encodes it in the result.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId, Settings

logger = logging.getLogger(__name__)

_COMMAND_TIMEOUT = 300  # seconds per command


@dataclass
class ValidationResult:
    """Result of running one or more validation commands."""

    passed: bool
    commands: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    failed_command: str | None = None
    return_code: int | None = None


def run(
    pipeline_id: "PipelineId",
    repo_root: Path,
    cfg: "Settings",
    dry_run: bool = True,
) -> ValidationResult:
    """
    Run the validation commands for *pipeline_id* in *repo_root*.

    Args:
        pipeline_id: Pipeline being validated.
        repo_root: Absolute path to the local repository clone.
        cfg: Validated RMS settings.
        dry_run: If True, skip execution and return a synthetic passed result.

    Returns:
        ValidationResult with passed=True if all commands exit 0.
    """
    commands = cfg.validation_commands_for(pipeline_id)

    if dry_run:
        logger.info(
            "validator [%s]: dry-run — skipping %d validation command(s)",
            pipeline_id,
            len(commands),
        )
        return ValidationResult(
            passed=True,
            commands=commands,
            outputs=["[dry-run: validation skipped]"] * len(commands),
        )

    outputs: list[str] = []
    for cmd in commands:
        logger.info("validator [%s]: running: %s", pipeline_id, cmd)
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=_COMMAND_TIMEOUT,
            )
            combined = _combine_output(result.stdout, result.stderr)
            outputs.append(combined)

            if result.returncode != 0:
                logger.warning(
                    "validator [%s]: command FAILED (rc=%d): %s\n%s",
                    pipeline_id,
                    result.returncode,
                    cmd,
                    combined[-2000:],
                )
                return ValidationResult(
                    passed=False,
                    commands=commands,
                    outputs=outputs,
                    failed_command=cmd,
                    return_code=result.returncode,
                )

            logger.info("validator [%s]: command OK: %s", pipeline_id, cmd)

        except subprocess.TimeoutExpired:
            msg = f"[timed out after {_COMMAND_TIMEOUT}s]"
            outputs.append(msg)
            logger.error("validator [%s]: command timed out: %s", pipeline_id, cmd)
            return ValidationResult(
                passed=False,
                commands=commands,
                outputs=outputs,
                failed_command=cmd,
                return_code=-1,
            )
        except OSError as exc:
            msg = f"[OS error: {exc}]"
            outputs.append(msg)
            logger.error("validator [%s]: OS error running %r: %s", pipeline_id, cmd, exc)
            return ValidationResult(
                passed=False,
                commands=commands,
                outputs=outputs,
                failed_command=cmd,
                return_code=-1,
            )

    return ValidationResult(
        passed=True,
        commands=commands,
        outputs=outputs,
    )


def _combine_output(stdout: str, stderr: str) -> str:
    """Merge stdout and stderr into a single readable string."""
    parts = []
    if stdout.strip():
        parts.append(stdout.rstrip())
    if stderr.strip():
        parts.append(stderr.rstrip())
    return "\n".join(parts) if parts else "(no output)"
