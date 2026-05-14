"""Safe read-side context builder for RAMS."""

from __future__ import annotations
import logging
from pathlib import Path
from repo_mgmt.patch_protocol import PathTraversalError

logger = logging.getLogger(__name__)
_MAX_FILE_BYTES = 256 * 1024


def load_context(affected_paths: list[str], repo_root: Path) -> dict[str, str]:
    """Read repo-relative file contents while rejecting traversal attempts."""
    real_root = repo_root.resolve()
    out = {}
    for rel in affected_paths:
        try:
            resolved = (real_root / rel).resolve()
            resolved.relative_to(real_root)
        except ValueError as exc:
            raise PathTraversalError(
                f"context path {rel!r} resolves outside repo root"
            ) from exc
        if not resolved.is_file():
            logger.warning("context_builder: file not found: %r", rel)
            continue
        if resolved.stat().st_size > _MAX_FILE_BYTES:
            logger.warning("context_builder: skipping oversized file: %r", rel)
            continue
        out[rel] = resolved.read_text(encoding="utf-8", errors="replace")
    return out
