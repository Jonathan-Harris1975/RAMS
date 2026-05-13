"""Canonical validation runner for RAMS."""

from __future__ import annotations
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId, Settings
DEFAULT_TIMEOUT_SECONDS = 300


@dataclass
class ValidationResult:
    passed: bool
    commands: list[str] = field(default_factory=list)
    output_tail: str = ""
    return_code: int = 0
    failed_command: str | None = None


def _tail_200(text: str) -> str:
    return "\n".join(text.splitlines()[-200:])


def run_commands(
    commands: list[str], cwd: Path, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
) -> ValidationResult:
    tail = ""
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                shell=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            return ValidationResult(
                False,
                commands,
                _tail_200(out + f"\nTIMEOUT after {timeout_seconds}s"),
                124,
                cmd,
            )
        tail = _tail_200(proc.stdout or "")
        if proc.returncode != 0:
            return ValidationResult(False, commands, tail, proc.returncode, cmd)
    return ValidationResult(True, commands, tail, 0, None)


def run(
    pipeline_id: "PipelineId",
    repo_root: Path,
    cfg: "Settings",
    dry_run: bool = True,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> ValidationResult:
    return run_commands(
        cfg.validation_commands_for(pipeline_id),
        cwd=repo_root,
        timeout_seconds=timeout_seconds,
    )
