"""Safe, bounded read-side context builder for RAMS."""

from __future__ import annotations

import logging
from pathlib import Path

from repo_mgmt.patch_protocol import PathTraversalError

logger = logging.getLogger(__name__)
_DEFAULT_MAX_FILE_BYTES = 256 * 1024
_DEFAULT_MAX_TOTAL_BYTES = 1024 * 1024
_DEFAULT_MAX_FILES = 20


def _looks_binary(data: bytes) -> bool:
    """Return True for obvious binary content without expensive detection."""
    return b"\x00" in data[:8192]


def load_context(
    affected_paths: list[str],
    repo_root: Path,
    *,
    max_files: int = _DEFAULT_MAX_FILES,
    max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
) -> dict[str, str]:
    """Read unique repo-relative text files within explicit eMicro limits."""
    real_root = repo_root.resolve()
    out: dict[str, str] = {}
    total_bytes = 0
    seen: set[str] = set()
    for rel in affected_paths:
        normalised = str(rel).replace("\\", "/")
        if normalised in seen:
            continue
        seen.add(normalised)
        if len(out) >= max_files:
            logger.warning("context_builder: max file count reached (%d)", max_files)
            break
        try:
            resolved = (real_root / normalised).resolve()
            resolved.relative_to(real_root)
        except ValueError as exc:
            raise PathTraversalError(
                f"context path {normalised!r} resolves outside repo root"
            ) from exc
        if not resolved.is_file():
            logger.warning("context_builder: file not found: %r", normalised)
            continue
        try:
            size = resolved.stat().st_size
        except OSError:
            logger.warning("context_builder: cannot stat file: %r", normalised)
            continue
        if size > max_file_bytes:
            logger.warning("context_builder: skipping oversized file: %r", normalised)
            continue
        remaining = max_total_bytes - total_bytes
        if remaining <= 0 or size > remaining:
            logger.warning("context_builder: total context limit reached")
            break
        try:
            data = resolved.read_bytes()
        except OSError:
            logger.warning("context_builder: cannot read file: %r", normalised)
            continue
        if len(data) > max_file_bytes or len(data) > remaining:
            logger.warning(
                "context_builder: file exceeded context budget: %r", normalised
            )
            continue
        if _looks_binary(data):
            logger.warning("context_builder: skipping binary file: %r", normalised)
            continue
        out[normalised] = data.decode("utf-8", errors="replace")
        total_bytes += len(data)
    return out
