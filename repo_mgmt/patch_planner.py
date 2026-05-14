"""AnchorPatch/v1 patch planner for RAMS."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repo_mgmt.patch_applier import PROTECTED_PATHS
from repo_mgmt.patch_protocol import (
    PathTraversalError,
    PatchSchemaError,
    is_protected,
    validate_patch,
)

if TYPE_CHECKING:
    from repo_mgmt.config import Settings
    from repo_mgmt.model_router import ModelRouter

logger = logging.getLogger(__name__)
_MAX_FILE_BYTES = 256 * 1024


class PatchPlanError(Exception):
    """Raised when a model response is not a valid bounded patch plan."""


SYSTEM_PROMPT = """
You are a deterministic code-patch planner for an autonomous
repository management system. You receive:
– a task description and required outcome
– the full contents of each affected file
– the AnchorPatch/v1 protocol specification
Your response MUST be a single valid JSON object conforming to
AnchorPatch/v1. No prose, no markdown fences, no explanation
outside the JSON.
Rules:
1. Only include files listed in affectedPaths.
2. anchorBefore must appear exactly once in the current file.
3. find must appear exactly once in the current file (replace/delete).
4. Do not rename files, add new dependencies, or change unrelated code.
5. Do not touch any file whose path matches a protected prefix
(blog/posts/, transcripts/, data/podcast-episodes.json) unless
this pipeline explicitly has authority over those paths.
6. If the required outcome cannot be safely achieved with a bounded
patch, return:
{"patchProtocol":"AnchorPatch/v1","changes":[],"reason":"<why>"}
"""


def plan(
    issue: dict[str, Any],
    target_repo: Path,
    pipeline_id: str,
    settings: "Settings",
    model_router: "ModelRouter",
) -> dict[str, Any]:
    """Request and validate a strict AnchorPatch/v1 plan for a code_fix issue."""
    task_id = str(issue.get("taskId", "<unknown>"))
    if issue.get("classification") != "code_fix":
        raise PatchPlanError(
            f"plan() called on non-code_fix issue (classification={issue.get('classification')!r})"
        )
    affected_paths = [str(path) for path in issue.get("affectedPaths", [])]
    context_files = _load_context(affected_paths, target_repo)
    prompt = _build_prompt(issue, context_files, pipeline_id)
    try:
        raw = model_router.complete(
            prompt=prompt, system=SYSTEM_PROMPT, max_tokens=4096
        )
    except Exception as exc:
        raise PatchPlanError(f"LLM call failed: {exc}") from exc
    patch_doc = _parse_plan(raw, task_id)
    _validate_plan_scope(patch_doc, affected_paths, pipeline_id)
    return patch_doc


def _parse_plan(raw: str, task_id: str) -> dict[str, Any]:
    """Parse a model response as strict JSON and validate AnchorPatch/v1."""
    text = raw.strip()
    if text.startswith("```") or text.endswith("```"):
        raise PatchPlanError("Model response must be strict JSON, not markdown fences")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PatchPlanError(
            f"Model response is not valid JSON: {exc}\nRaw (first 400 chars): {raw[:400]}"
        ) from exc
    if isinstance(data, dict) and ("operations" in data or "taskId" in data):
        raise PatchPlanError(
            "Planner must emit AnchorPatch/v1 directly, not taskId/operations"
        )
    try:
        return validate_patch(data)
    except PatchSchemaError as exc:
        raise PatchPlanError(
            f"Invalid AnchorPatch/v1 plan for {task_id!r}: {exc}"
        ) from exc


def _validate_plan_scope(
    patch_doc: dict[str, Any], affected_paths: list[str], pipeline_id: str
) -> None:
    """Ensure the plan stays inside affectedPaths and outside protected paths."""
    allowed = set(affected_paths)
    protected = PROTECTED_PATHS.get(pipeline_id, frozenset())
    for index, change in enumerate(patch_doc.get("changes", [])):
        if not isinstance(change, dict):
            continue
        file_path = str(change.get("file", ""))
        if file_path not in allowed:
            raise PatchPlanError(
                f"change[{index}] targets {file_path!r}, which is not in affectedPaths"
            )
        if is_protected(file_path, protected):
            raise PatchPlanError(
                f"change[{index}] targets protected path {file_path!r} for {pipeline_id!r}"
            )


def _load_context(affected_paths: list[str], repo_root: Path) -> dict[str, str]:
    """Load bounded text context from affected paths only."""
    real_root = repo_root.resolve()
    out: dict[str, str] = {}
    for rel in affected_paths:
        try:
            resolved = (real_root / rel).resolve()
            resolved.relative_to(real_root)
        except ValueError as exc:
            logger.warning("patch_planner: rejecting path outside repo: %r", rel)
            raise PathTraversalError(
                f"context path {rel!r} resolves outside repo root"
            ) from exc
        if not resolved.is_file():
            logger.warning("patch_planner: file not found: %r", rel)
            continue
        if resolved.stat().st_size > _MAX_FILE_BYTES:
            logger.warning("patch_planner: skipping oversized file: %r", rel)
            continue
        out[rel] = resolved.read_text(encoding="utf-8", errors="replace")
    return out


def _build_prompt(
    issue: dict[str, Any], context_files: dict[str, str], pipeline_id: str
) -> str:
    """Build the JSON prompt passed to the model."""
    return json.dumps(
        {
            "pipeline": pipeline_id,
            "task": issue,
            "contextFiles": context_files,
            "outputContract": {
                "patchProtocol": "AnchorPatch/v1",
                "changes": [],
                "reason": "<why if no bounded patch is safe>",
            },
        },
        indent=2,
        ensure_ascii=False,
    )
