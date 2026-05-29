"""
Patch applier for the Repo Management Suite.

Applies AnchorPatch/v1 documents to a local repository clone while enforcing
path traversal, protected-path, anchor, exact-find, and atomic-write safety
contracts before any mutation occurs.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from repo_mgmt.patch_protocol import (
    PathTraversalError,
    PatchSchemaError,
    ProtectedPathError,
    is_protected,
    validate_patch,
)

logger = logging.getLogger(__name__)


class PatchApplyError(Exception):
    """Raised when a patch operation cannot be applied cleanly."""


PROTECTED_PATHS: dict[str, frozenset[str]] = {
    "mobile-ux": frozenset(
        [
            "blog/posts/",
            "blog/posts.json",
            "transcripts/",
            "data/podcast-episodes.json",
            "assets/js/podcast-transcripts.min.js",
            "functions/transcripts/",
        ]
    ),
    # On-brand findings are future-guidance by default (editorial / prompt-level).
    # These paths are an explicit backstop so the applier refuses a code_fix patch
    # targeting content files even if the issue_normaliser classification was wrong.
    "on-brand": frozenset(
        [
            "blog/posts/",
            "blog/posts.json",
            "transcripts/",
            "data/podcast-episodes.json",
            "assets/js/podcast-transcripts.min.js",
        ]
    ),
    "seo-aeo-geo": frozenset(),
}


def apply(
    patch_doc: dict[str, Any],
    target_repo: Path,
    dry_run: bool = True,
    pipeline_id: str = "",
) -> list[str]:
    """
    Apply all changes in an AnchorPatch/v1 document to *target_repo*.

    Args:
        patch_doc: AnchorPatch/v1 document.
        target_repo: Local repository root.
        dry_run: When true, validate matches and log actions without writes.
        pipeline_id: Active pipeline, used for protected-path enforcement.

    Returns:
        Repo-relative paths that were or would be modified.

    Raises:
        PathTraversalError: If a change escapes the repo root.
        ProtectedPathError: If a change targets a protected path.
        PatchApplyError: If matching or application fails.
    """
    try:
        validate_patch(patch_doc)
    except PatchSchemaError as exc:
        message = str(exc)
        if "path traversal" in message or "absolute paths" in message:
            raise PathTraversalError(message) from exc
        raise PatchApplyError(f"Invalid patch document: {exc}") from exc

    protected = PROTECTED_PATHS.get(pipeline_id, frozenset())
    real_root = target_repo.resolve()
    changes: list[dict[str, Any]] = patch_doc.get("changes", [])
    modified: list[str] = []

    for index, change in enumerate(changes):
        rel_path: str = change["file"]
        operation: str = change["operation"]

        try:
            resolved = (real_root / rel_path).resolve()
            resolved.relative_to(real_root)
        except ValueError as exc:
            raise PathTraversalError(
                f"change[{index}] path {rel_path!r} resolves outside repo root - rejected"
            ) from exc

        if is_protected(rel_path, protected):
            raise ProtectedPathError(
                f"change[{index}] path {rel_path!r} is protected for pipeline {pipeline_id!r}"
            )

        prefix = f"change[{index}] ({operation} {rel_path})"
        if operation == "replace":
            _apply_replace(resolved, change, prefix, dry_run)
        elif operation == "insert_after":
            _apply_insert_after(resolved, change, prefix, dry_run)
        elif operation == "delete":
            _apply_delete(resolved, change, prefix, dry_run)
        else:
            raise PatchApplyError(f"{prefix}: unsupported operation {operation!r}")
        modified.append(rel_path)

    mode = "DRY-RUN" if dry_run else "APPLIED"
    logger.info(
        "patch_applier: %s - %d change(s) [pipeline=%s]",
        mode,
        len(changes),
        pipeline_id or "<none>",
    )
    return modified


def _read_file(abs_path: Path, prefix: str) -> str:
    """Read a UTF-8 text file or raise PatchApplyError."""
    if not abs_path.is_file():
        raise PatchApplyError(f"{prefix}: file not found: {abs_path}")
    return abs_path.read_text(encoding="utf-8")


def _atomic_write(abs_path: Path, content: str) -> None:
    """Write *content* atomically through a sibling .rms.tmp file."""
    tmp_path = abs_path.with_suffix(abs_path.suffix + ".rms.tmp")
    try:
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, abs_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _require_unique(text: str, needle: str, label: str, prefix: str) -> None:
    """Require *needle* to occur exactly once in *text*."""
    count = text.count(needle)
    if count == 0:
        raise PatchApplyError(
            f"{prefix}: {label} string not found\n  {label}={needle[:120]!r}"
        )
    if count > 1:
        raise PatchApplyError(
            f"{prefix}: {label} string matches {count} locations - must be unique\n"
            f"  {label}={needle[:120]!r}"
        )


def _validate_anchor(original: str, change: dict[str, Any], prefix: str) -> None:
    """Require anchorBefore to be present exactly once before mutation."""
    anchor_before = str(change.get("anchorBefore", ""))
    _require_unique(original, anchor_before, "anchorBefore", prefix)


def _apply_replace(
    abs_path: Path, change: dict[str, Any], prefix: str, dry_run: bool
) -> None:
    """Apply a replace operation using unique anchor and unique find text."""
    find: str = change["find"]
    replacement: str = change.get("replace", "")
    original = _read_file(abs_path, prefix)
    _validate_anchor(original, change, prefix)
    _require_unique(original, find, "find", prefix)

    new_content = original.replace(find, replacement, 1)
    if dry_run:
        logger.info("%s: [dry-run] would replace 1 occurrence", prefix)
        return
    _atomic_write(abs_path, new_content)
    logger.info("%s: replaced 1 occurrence", prefix)


def _apply_insert_after(
    abs_path: Path, change: dict[str, Any], prefix: str, dry_run: bool
) -> None:
    """Apply an insert_after operation using unique anchor and find text."""
    find: str = change["find"]
    replacement: str = change.get("replace", "")
    original = _read_file(abs_path, prefix)
    _validate_anchor(original, change, prefix)
    _require_unique(original, find, "find", prefix)

    new_content = original.replace(find, find + replacement, 1)
    if dry_run:
        logger.info("%s: [dry-run] would insert after matched text", prefix)
        return
    _atomic_write(abs_path, new_content)
    logger.info("%s: inserted after matched text", prefix)


def _apply_delete(
    abs_path: Path, change: dict[str, Any], prefix: str, dry_run: bool
) -> None:
    """Apply a delete operation by removing one exact find match only."""
    find: str = change["find"]
    original = _read_file(abs_path, prefix)
    _validate_anchor(original, change, prefix)
    _require_unique(original, find, "find", prefix)

    new_content = original.replace(find, "", 1)
    if dry_run:
        logger.info("%s: [dry-run] would delete matched text", prefix)
        return
    _atomic_write(abs_path, new_content)
    logger.info("%s: deleted matched text", prefix)
