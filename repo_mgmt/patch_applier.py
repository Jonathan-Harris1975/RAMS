"""
Patch applier for the Repo Management Suite.

Applies an AnchorPatch/v1 document to a local repository clone.

Safety checks enforced before any file read or write:
  1. Resolve target path using Path.resolve().relative_to(target_repo.resolve())
     — the ONLY safe boundary check (no unsafe startswith).
  2. Check protected path prefixes via patch_protocol.is_protected().
  3. anchorBefore must appear in file content when supplied.
  4. For replace/insert_after, find must appear exactly once.
  5. Writes are atomic: write to <file>.rms.tmp then os.replace().

Supported operations:
  replace      — find unique text, replace with new text
  insert_after — insert text immediately after the unique anchor string
  delete       — if find is empty, delete the whole file;
                 if find is non-empty, remove that exact text (must appear once)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from repo_mgmt.patch_protocol import (
    PathTraversalError,
    ProtectedPathError,
    PatchSchemaError,
    is_protected,
    validate_patch,
)

logger = logging.getLogger(__name__)


class PatchApplyError(Exception):
    """Raised when a patch operation cannot be applied cleanly."""


# Protected path sets keyed by pipeline_id — applier-layer enforcement
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
    "on-brand": frozenset(),
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
        patch_doc: AnchorPatch/v1 dict (validated internally).
        target_repo: Absolute path to the local repository root.
        dry_run: If True, log intended changes without touching the filesystem.
        pipeline_id: Pipeline being run (controls which paths are protected).

    Returns:
        List of repo-relative paths that were (or would be) modified.

    Raises:
        PathTraversalError: If any change path escapes the repo root.
        ProtectedPathError: If any change path is in the protected set.
        PatchApplyError: If a change cannot be applied cleanly.
    """
    try:
        validate_patch(patch_doc)
    except PatchSchemaError as exc:
        raise PatchApplyError(f"Invalid patch document: {exc}") from exc

    protected = PROTECTED_PATHS.get(pipeline_id, frozenset())
    real_root = target_repo.resolve()
    changes: list[dict[str, Any]] = patch_doc.get("changes", [])
    modified: list[str] = []

    for i, change in enumerate(changes):
        rel_path: str = change["file"]
        operation: str = change["operation"]

        # ── Safety gate 1: path traversal — safe boundary check ────────────
        try:
            resolved = (real_root / rel_path).resolve()
            resolved.relative_to(real_root)  # raises ValueError if outside
        except ValueError:
            raise PathTraversalError(
                f"change[{i}] path {rel_path!r} resolves outside repo root — rejected"
            )

        # ── Safety gate 2: protected paths ─────────────────────────────────
        if is_protected(rel_path, protected):
            raise ProtectedPathError(
                f"change[{i}] path {rel_path!r} is protected for pipeline {pipeline_id!r}"
            )

        abs_path = resolved
        prefix = f"change[{i}] ({operation} {rel_path})"

        if operation == "replace":
            _apply_replace(abs_path, change, prefix, dry_run)
        elif operation == "insert_after":
            _apply_insert_after(abs_path, change, prefix, dry_run)
        elif operation == "delete":
            _apply_delete(abs_path, change, prefix, dry_run)
        else:
            raise PatchApplyError(f"{prefix}: unsupported operation {operation!r}")

        modified.append(rel_path)

    mode = "DRY-RUN" if dry_run else "APPLIED"
    logger.info(
        "patch_applier: %s — %d change(s) [pipeline=%s]",
        mode, len(changes), pipeline_id or "<none>",
    )
    return modified


# ── Helpers ────────────────────────────────────────────────────────────────


def _read_file(abs_path: Path, prefix: str) -> str:
    """Read a file, raising PatchApplyError if missing."""
    if not abs_path.is_file():
        raise PatchApplyError(f"{prefix}: file not found: {abs_path}")
    return abs_path.read_text(encoding="utf-8")


def _atomic_write(abs_path: Path, content: str) -> None:
    """Write *content* atomically via a .rms.tmp sibling then os.replace."""
    tmp_path = abs_path.with_suffix(abs_path.suffix + ".rms.tmp")
    try:
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, abs_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _apply_replace(
    abs_path: Path, change: dict[str, Any], prefix: str, dry_run: bool
) -> None:
    """Apply a 'replace' change: swap unique find-text with replacement."""
    anchor_before: str = change.get("anchorBefore", "")
    find: str = change["find"]
    replacement: str = change.get("replace", "")

    original = _read_file(abs_path, prefix)

    if anchor_before and anchor_before not in original:
        raise PatchApplyError(
            f"{prefix}: anchorBefore string not found in file\n"
            f"  anchorBefore={anchor_before[:120]!r}"
        )
    count = original.count(find)
    if count == 0:
        raise PatchApplyError(f"{prefix}: find string not found\n  find={find[:120]!r}")
    if count > 1:
        raise PatchApplyError(
            f"{prefix}: find string matches {count} locations — must be unique\n"
            f"  find={find[:120]!r}"
        )

    new_content = original.replace(find, replacement, 1)
    if dry_run:
        logger.info("%s: [dry-run] would replace 1 occurrence", prefix)
    else:
        _atomic_write(abs_path, new_content)
        logger.info("%s: replaced 1 occurrence", prefix)


def _apply_insert_after(
    abs_path: Path, change: dict[str, Any], prefix: str, dry_run: bool
) -> None:
    """Apply an 'insert_after' change: append text immediately after anchor."""
    anchor_before: str = change.get("anchorBefore", "")
    find: str = change["find"]
    replacement: str = change.get("replace", "")

    original = _read_file(abs_path, prefix)

    if anchor_before and anchor_before not in original:
        raise PatchApplyError(
            f"{prefix}: anchorBefore string not found in file\n"
            f"  anchorBefore={anchor_before[:120]!r}"
        )
    count = original.count(find)
    if count == 0:
        raise PatchApplyError(f"{prefix}: find string not found\n  find={find[:120]!r}")
    if count > 1:
        raise PatchApplyError(
            f"{prefix}: find string matches {count} locations — must be unique\n"
            f"  find={find[:120]!r}"
        )

    new_content = original.replace(find, find + replacement, 1)
    if dry_run:
        logger.info("%s: [dry-run] would insert after anchor", prefix)
    else:
        _atomic_write(abs_path, new_content)
        logger.info("%s: inserted after anchor", prefix)


def _apply_delete(
    abs_path: Path, change: dict[str, Any], prefix: str, dry_run: bool
) -> None:
    """
    Apply a 'delete' change.

    - If find is empty (or absent): delete the entire file.
    - If find is non-empty: remove that exact text from the file (must appear once).
    """
    find: str = change.get("find", "")

    if not find:
        # Whole-file deletion
        if dry_run:
            logger.info("%s: [dry-run] would delete file %s", prefix, abs_path)
        else:
            if abs_path.exists():
                abs_path.unlink()
                logger.info("%s: deleted file %s", prefix, abs_path)
            else:
                logger.warning("%s: file already absent: %s", prefix, abs_path)
        return

    # Text removal
    original = _read_file(abs_path, prefix)
    count = original.count(find)
    if count == 0:
        raise PatchApplyError(
            f"{prefix}: find string not found\n  find={find[:120]!r}"
        )
    if count > 1:
        raise PatchApplyError(
            f"{prefix}: find string matches {count} locations — must be unique\n"
            f"  find={find[:120]!r}"
        )

    new_content = original.replace(find, "", 1)
    if dry_run:
        logger.info("%s: [dry-run] would delete matched text", prefix)
    else:
        _atomic_write(abs_path, new_content)
        logger.info("%s: deleted matched text", prefix)
