"""
Context builder for the Repo Management Suite.

Loads the exact file contents required by the patch planner for a given
set of affected paths.  Rejects any path that resolves outside the repo
root, and avoids loading large unrelated files.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES: int = 256 * 1024  # 256 KB per file — avoid huge unrelated context


def load_context(
    affected_paths: list[str],
    repo_root: Path,
) -> dict[str, str]:
    """
    Load the content of each path in *affected_paths* from *repo_root*.

    Paths that resolve outside *repo_root*, do not exist, or exceed the
    size limit are skipped with a warning log — they are NOT included in
    the returned dict.

    Args:
        affected_paths: Repo-relative path strings from a NormalisedIssue.
        repo_root: Absolute path to the local repository clone.

    Returns:
        Dict mapping repo-relative path to UTF-8 file content string.
    """
    real_root = os.path.realpath(repo_root)
    context: dict[str, str] = {}

    for rel in affected_paths:
        resolved = os.path.realpath(Path(real_root) / rel)

        if not resolved.startswith(real_root):
            logger.warning(
                "context_builder: rejecting path outside repo root: %r", rel
            )
            continue

        abs_path = Path(resolved)
        if not abs_path.is_file():
            logger.warning("context_builder: file not found: %r", rel)
            continue

        size = abs_path.stat().st_size
        if size > _MAX_FILE_BYTES:
            logger.warning(
                "context_builder: skipping oversized file %r (%d bytes > %d limit)",
                rel, size, _MAX_FILE_BYTES,
            )
            continue

        try:
            context[rel] = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("context_builder: could not read %r: %s", rel, exc)

    return context
