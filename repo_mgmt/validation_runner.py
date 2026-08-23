"""Sequential, memory-bounded validation runner for RAMS."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shlex
from typing import TYPE_CHECKING

from repo_mgmt.process_runner import run_bounded

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId, Settings

DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_OUTPUT_LINES = 200
DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024


@dataclass
class ValidationResult:
    """Outcome from a sequential validation-command run."""

    passed: bool
    commands: list[str] = field(default_factory=list)
    output_tail: str = ""
    return_code: int = 0
    failed_command: str | None = None


_FORBIDDEN_SHELL_TOKENS = {"|", "||", "&&", ";", "&", ">", ">>", "<", "<<", "2>", "2>>"}


def _argv_for_validation(cmd: str) -> list[str]:
    """Parse one governed validation command without invoking a shell."""
    if "\n" in cmd or "\r" in cmd or "`" in cmd or "$(" in cmd:
        raise ValueError("shell control syntax is not allowed in validation commands")
    try:
        argv = shlex.split(cmd, posix=True)
    except ValueError as exc:
        raise ValueError(f"invalid validation command quoting: {exc}") from exc
    if not argv:
        raise ValueError("validation command is empty")
    if any(token in _FORBIDDEN_SHELL_TOKENS for token in argv):
        raise ValueError("shell operators are not allowed in validation commands; configure each command separately")
    return argv


def _run_command(
    cmd: str,
    cwd: Path,
    timeout_seconds: int,
    max_output_lines: int,
    max_output_bytes: int,
) -> tuple[int, str, bool]:
    """Run one operator-configured command as explicit argv with bounded diagnostics."""
    try:
        argv = _argv_for_validation(cmd)
    except ValueError as exc:
        return 2, f"VALIDATION CONFIG ERROR: {exc}", False
    result = run_bounded(
        argv,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        max_output_lines=max_output_lines,
        max_output_bytes=max_output_bytes,
        output_label="validation",
    )
    return result.return_code, result.output, result.timed_out


def run_commands(
    commands: list[str],
    cwd: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    *,
    max_output_lines: int = DEFAULT_MAX_OUTPUT_LINES,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> ValidationResult:
    """Run commands sequentially while bounding output retained in memory."""
    tail = ""
    for cmd in commands:
        return_code, tail, _ = _run_command(
            cmd,
            cwd,
            timeout_seconds,
            max_output_lines,
            max_output_bytes,
        )
        if return_code != 0:
            return ValidationResult(False, commands, tail, return_code, cmd)
    return ValidationResult(True, commands, tail, 0, None)


def run(
    pipeline_id: PipelineId,
    repo_root: Path,
    cfg: Settings,
    dry_run: bool = True,
    timeout_seconds: int | None = None,
) -> ValidationResult:
    """Run configured validation commands for one target repo."""
    _ = dry_run
    return run_commands(
        cfg.validation_commands_for(pipeline_id),
        cwd=repo_root,
        timeout_seconds=timeout_seconds or cfg.rms_validation_timeout_seconds,
        max_output_lines=cfg.rms_validation_output_max_lines,
        max_output_bytes=cfg.rms_validation_output_max_bytes,
    )
