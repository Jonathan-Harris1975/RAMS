"""
Patch applier for the Repo Management Suite.

Applies a PatchPlan to a local repository clone using atomic operations:
  - replace / insert_after: read → mutate → write
  - create: write new file (fails if file already exists, unless --overwrite)
  - delete: remove file

All writes are performed only when dry_run=False.
Raises PatchApplyError with context on any failure.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PatchApplyError(Exception):
    """Raised when a patch operation cannot be applied cleanly."""


def apply(
    patch_plan: dict[str, Any],
    repo_root: Path,
    dry_run: bool = True,
) -> list[str]:
    """
    Apply all operations in *patch_plan* to *repo_root*.

    Args:
        patch_plan: Validated PatchPlan dict from patch_planner.plan().
        repo_root: Absolute path to the local repository clone.
        dry_run: If True, log what would happen but make no filesystem changes.

    Returns:
        List of relative paths that were (or would be) modified.

    Raises:
        PatchApplyError: If any operation fails (e.g. search string not found,
                         file missing for replace, path traversal detected).
    """
    task_id = patch_plan.get("taskId", "<unknown>")
    ops: list[dict[str, Any]] = patch_plan.get("operations", [])
    modified: list[str] = []

    for i, op in enumerate(ops):
        action: str = op["action"]
        rel_path: str = op["path"]

        # Security: reject path traversal
        abs_path = (repo_root / rel_path).resolve()
        if not str(abs_path).startswith(str(repo_root.resolve())):
            raise PatchApplyError(
                f"[{task_id}] op {i}: path {rel_path!r} escapes repo root — rejected"
            )

        prefix = f"[{task_id}] op {i} ({action} {rel_path})"

        if action == "replace":
            _apply_replace(abs_path, op, prefix, dry_run)
            modified.append(rel_path)

        elif action == "insert_after":
            _apply_insert_after(abs_path, op, prefix, dry_run)
            modified.append(rel_path)

        elif action == "create":
            _apply_create(abs_path, op, prefix, dry_run)
            modified.append(rel_path)

        elif action == "delete":
            _apply_delete(abs_path, prefix, dry_run)
            modified.append(rel_path)

        else:
            raise PatchApplyError(f"{prefix}: unknown action {action!r}")

    mode = "DRY-RUN" if dry_run else "APPLIED"
    logger.info("patch_applier [%s]: %s — %d ops on %d files", task_id, mode, len(ops), len(modified))
    return modified


# ── Operation implementations ──────────────────────────────────────────────

def _apply_replace(
    abs_path: Path,
    op: dict[str, Any],
    prefix: str,
    dry_run: bool,
) -> None:
    search: str = op["search"]
    replacement: str = op.get("replacement", "")

    if not abs_path.is_file():
        raise PatchApplyError(f"{prefix}: file not found: {abs_path}")

    original = abs_path.read_text(encoding="utf-8")
    count = original.count(search)
    if count == 0:
        raise PatchApplyError(
            f"{prefix}: search string not found in {abs_path}\n"
            f"  search={search[:120]!r}"
        )
    if count > 1:
        raise PatchApplyError(
            f"{prefix}: search string matches {count} locations in {abs_path} — "
            "must be unique. Widen the search string."
        )

    new_content = original.replace(search, replacement, 1)
    if dry_run:
        logger.info("%s: [dry-run] would replace 1 occurrence", prefix)
    else:
        abs_path.write_text(new_content, encoding="utf-8")
        logger.info("%s: replaced 1 occurrence", prefix)


def _apply_insert_after(
    abs_path: Path,
    op: dict[str, Any],
    prefix: str,
    dry_run: bool,
) -> None:
    search: str = op["search"]
    replacement: str = op.get("replacement", "")

    if not abs_path.is_file():
        raise PatchApplyError(f"{prefix}: file not found: {abs_path}")

    original = abs_path.read_text(encoding="utf-8")
    count = original.count(search)
    if count == 0:
        raise PatchApplyError(
            f"{prefix}: anchor string not found in {abs_path}\n"
            f"  search={search[:120]!r}"
        )
    if count > 1:
        raise PatchApplyError(
            f"{prefix}: anchor string matches {count} locations in {abs_path} — must be unique"
        )

    new_content = original.replace(search, search + replacement, 1)
    if dry_run:
        logger.info("%s: [dry-run] would insert after anchor", prefix)
    else:
        abs_path.write_text(new_content, encoding="utf-8")
        logger.info("%s: inserted after anchor", prefix)


def _apply_create(
    abs_path: Path,
    op: dict[str, Any],
    prefix: str,
    dry_run: bool,
) -> None:
    content: str = op.get("content", "")

    if abs_path.exists():
        raise PatchApplyError(
            f"{prefix}: file already exists: {abs_path}. "
            "Use 'replace' to modify existing files."
        )

    if dry_run:
        logger.info("%s: [dry-run] would create file (%d chars)", prefix, len(content))
    else:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")
        logger.info("%s: created file (%d chars)", prefix, len(content))


def _apply_delete(
    abs_path: Path,
    prefix: str,
    dry_run: bool,
) -> None:
    if not abs_path.exists():
        raise PatchApplyError(f"{prefix}: file not found: {abs_path}")

    if dry_run:
        logger.info("%s: [dry-run] would delete file", prefix)
    else:
        abs_path.unlink()
        logger.info("%s: deleted file", prefix)
