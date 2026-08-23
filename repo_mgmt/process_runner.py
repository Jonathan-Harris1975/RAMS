"""Linux/container subprocess runner with bounded diagnostic output."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

_READ_CHUNK_BYTES = 8192


@dataclass(frozen=True)
class BoundedProcessResult:
    """Result from a subprocess whose retained output is strictly bounded."""

    return_code: int
    output: str
    timed_out: bool
    truncated: bool


class _BoundedTail:
    """Retain only the newest bytes from a process stream."""

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._buffer = bytearray()
        self.truncated = False

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        if len(chunk) >= self._max_bytes:
            self._buffer = bytearray(chunk[-self._max_bytes :])
            self.truncated = True
            return
        overflow = len(self._buffer) + len(chunk) - self._max_bytes
        if overflow > 0:
            del self._buffer[:overflow]
            self.truncated = True
        self._buffer.extend(chunk)

    def render(
        self,
        max_lines: int,
        prefix: str,
        *,
        suffix: str | None = None,
    ) -> str:
        """Render a byte- and line-bounded tail, including status rows."""
        text = bytes(self._buffer).decode("utf-8", errors="replace")
        source_lines = text.splitlines()
        suffix_lines = [suffix] if suffix else []
        marker_needed = self.truncated

        # The truncation marker itself counts towards the configured line cap.
        if len(source_lines) + len(suffix_lines) > max_lines:
            marker_needed = True
        reserved = len(suffix_lines) + (1 if marker_needed else 0)
        content_limit = max(0, max_lines - reserved)
        selected = source_lines[-content_limit:] if content_limit else []
        if len(selected) < len(source_lines):
            marker_needed = True
            self.truncated = True
            reserved = len(suffix_lines) + 1
            content_limit = max(0, max_lines - reserved)
            selected = source_lines[-content_limit:] if content_limit else []

        marker = [f"[{prefix} output truncated]"] if marker_needed else []

        def _compose() -> str:
            return "\n".join([*marker, *selected, *suffix_lines])

        rendered = _compose()
        while selected and len(rendered.encode("utf-8")) > self._max_bytes:
            if len(selected) > 1:
                selected.pop(0)
            else:
                fixed = "\n".join([*marker, *suffix_lines]).encode("utf-8")
                separators = int(bool(marker)) + int(bool(suffix_lines))
                budget = max(0, self._max_bytes - len(fixed) - separators)
                selected[0] = (
                    selected[0]
                    .encode("utf-8")[-budget:]
                    .decode("utf-8", errors="replace")
                    if budget
                    else ""
                )
                if not selected[0]:
                    selected.clear()
            marker_needed = True
            self.truncated = True
            marker = [f"[{prefix} output truncated]"]
            rendered = _compose()

        # Configured minima make this branch theoretical, but keep the contract strict.
        raw = rendered.encode("utf-8")
        if len(raw) > self._max_bytes:
            self.truncated = True
            rendered = raw[-self._max_bytes :].decode("utf-8", errors="replace")
        return rendered


def terminate_process_tree(proc: subprocess.Popen[bytes]) -> None:
    """Terminate a process group, escalating to SIGKILL when required."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait(timeout=2)


def run_bounded(
    command: str | Sequence[str],
    *,
    cwd: Path | None,
    timeout_seconds: float,
    max_output_bytes: int,
    max_output_lines: int,
    env: Mapping[str, str] | None = None,
    output_label: str = "process",
) -> BoundedProcessResult:
    """Run one command while draining and retaining only a bounded output tail."""
    proc = subprocess.Popen(  # noqa: S603 - callers control fixed argv or operator env commands
        command,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    os.set_blocking(fd, False)
    selector = selectors.DefaultSelector()
    selector.register(fd, selectors.EVENT_READ)
    tail = _BoundedTail(max_output_bytes)
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    try:
        while True:
            if time.monotonic() >= deadline:
                timed_out = True
                terminate_process_tree(proc)
                break
            events = selector.select(timeout=0.1)
            for key, _ in events:
                try:
                    chunk = os.read(key.fd, _READ_CHUNK_BYTES)
                except BlockingIOError:
                    continue
                tail.append(chunk)
            if proc.poll() is not None:
                while True:
                    try:
                        chunk = os.read(fd, _READ_CHUNK_BYTES)
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    tail.append(chunk)
                break
    finally:
        selector.close()
        proc.stdout.close()
        if proc.poll() is None:
            terminate_process_tree(proc)
    return_code = 124 if timed_out else int(proc.returncode or 0)
    timeout_status = f"TIMEOUT after {timeout_seconds:g}s" if timed_out else None
    output = tail.render(max_output_lines, output_label, suffix=timeout_status)
    return BoundedProcessResult(
        return_code=return_code,
        output=output,
        timed_out=timed_out,
        truncated=tail.truncated,
    )
