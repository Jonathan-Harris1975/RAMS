"""
Validation runner for the Repo Management Suite.

Runs the pipeline's validation commands sequentially in the repository working
directory.  Stops on the first non-zero exit code.  Never raises on command
failure — the result is captured in ValidationResult.

Returns the last 200 lines of combined stdout+stderr as output_tail.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

from pathlib import Path

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS: int = 300
_TAIL_LINES: int = 200


@dataclass
class ValidationResult:
    """Result of running one or more validation commands."""
    passed: bool
    output_tail: str


def run_commands(commands: list[str], cwd: Path) -> ValidationResult:
    """
    Run *commands* sequentially in *cwd*, stopping on first failure.

    Args:
        commands: Ordered list of shell command strings to execute.
        cwd: Working directory (must be the target repo root).

    Returns:
        ValidationResult with passed=True only if all commands exit 0.
    """
    all_lines: list[str] = []

    for cmd in commands:
        logger.info("validation_runner: running %r in %s", cmd, cwd)
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            timeout_msg = f"[TIMEOUT after {_TIMEOUT_SECONDS}s]: {cmd}"
            logger.error("validation_runner: %s", timeout_msg)
            all_lines.append(timeout_msg)
            tail = "\n".join(all_lines[-_TAIL_LINES:])
            return ValidationResult(passed=False, output_tail=tail)

        combined = result.stdout + result.stderr
        all_lines.extend(combined.splitlines())

        if result.returncode != 0:
            logger.warning(
                "validation_runner: command exited %d: %r", result.returncode, cmd
            )
            tail = "\n".join(all_lines[-_TAIL_LINES:])
            return ValidationResult(passed=False, output_tail=tail)

        logger.info("validation_runner: command passed: %r", cmd)

    tail = "\n".join(all_lines[-_TAIL_LINES:])
    return ValidationResult(passed=True, output_tail=tail)
