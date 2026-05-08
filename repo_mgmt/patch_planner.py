"""
Patch planner for the Repo Management Suite.

For each code_fix NormalisedIssue, loads file context, builds a prompt,
calls the LLM, and parses the response into a validated plan dict.

Plan format (returned by plan() and _parse_plan()):
  {
    "taskId": str,
    "operations": [
      {
        "action": "replace" | "insert_after" | "delete" | "create",
        "path": "repo-relative path",
        "search": "unique text to find (required for replace/insert_after)",
        "replacement": "replacement text (required for replace/insert_after)",
        "content": "file content (required for create)",
        "rationale": "reason"
      }
    ]
  }

System prompt instructs the model to return this exact JSON format.

Public API:
  plan(issue, target_repo, pipeline_id, settings, model_router) -> dict
  _parse_plan(raw, task_id) -> dict
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repo_mgmt.config import Settings
    from repo_mgmt.model_router import ModelRouter

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 256 * 1024  # 256 KB per file
_VALID_ACTIONS = frozenset(["replace", "insert_after", "delete", "create"])


# ── Custom exception ───────────────────────────────────────────────────────


class PatchPlanError(Exception):
    """Raised when patch planning fails (model error, parse error, or invalid plan)."""


# ── System prompt ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a precise, minimal-footprint code-fix engineer integrated into an autonomous repository management pipeline.

Your task: produce a JSON patch plan that resolves a single repository finding.

Output rules (STRICT):
1. Output ONLY valid JSON — no preamble, no markdown fences, no trailing text.
2. The root object must have exactly two keys: "taskId" and "operations".
3. Each operation must have: "action", "path", and optional "search", "replacement", "content", "rationale".
4. "action" must be one of: "replace", "insert_after", "delete", "create".
5. For "replace" and "insert_after": "search" must be a verbatim, unique substring of the file.
6. For "create": "content" must contain the full file content.
7. Make the smallest possible change to achieve the requiredOutcome.
8. Only modify files listed in affectedPaths.
9. If no safe, bounded patch is possible, return: {"taskId": "<id>", "operations": []}
"""


# ── Public API ─────────────────────────────────────────────────────────────


def plan(
    issue: dict[str, Any],
    target_repo: Path,
    pipeline_id: str,
    settings: "Settings",
    model_router: "ModelRouter",
) -> dict[str, Any]:
    """
    Generate a patch plan dict for a code_fix *issue*.

    Args:
        issue: NormalisedIssue dict with classification='code_fix'.
        target_repo: Absolute path to the local repository clone.
        pipeline_id: Active pipeline identifier.
        settings: Validated RMS settings.
        model_router: Initialised ModelRouter for LLM calls.

    Returns:
        Validated plan dict with "taskId" and "operations" keys.

    Raises:
        PatchPlanError: If the issue is not a code_fix, the LLM call fails,
                        or the response cannot be parsed into a valid plan.
    """
    task_id: str = issue.get("taskId", "<unknown>")
    classification = str(issue.get("classification", ""))

    if classification != "code_fix":
        raise PatchPlanError(
            f"plan() called on non-code_fix issue (classification={classification!r})"
        )

    # Load file context from target_repo
    affected_paths: list[str] = issue.get("affectedPaths", [])
    context_files = _load_context(affected_paths, target_repo)

    prompt = _build_prompt(issue, context_files, pipeline_id)

    try:
        raw = model_router.complete(
            prompt=prompt,
            system=SYSTEM_PROMPT,
            max_tokens=4096,
        )
    except Exception as exc:
        raise PatchPlanError(f"LLM call failed: {exc}") from exc

    return _parse_plan(raw, task_id)


def _parse_plan(raw: str, task_id: str) -> dict[str, Any]:
    """
    Parse and validate a raw LLM response into a plan dict.

    Args:
        raw: Raw response string from model_router.complete().
        task_id: Task identifier — used to populate/verify the taskId field.

    Returns:
        Validated plan dict with "taskId" and "operations" keys.

    Raises:
        PatchPlanError: With specific messages for:
            - "not valid JSON"
            - "no operations"
            - "unknown action"
            - "missing 'search'"
            - "missing 'content'"
    """
    # Strip markdown fences if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned.strip(), flags=re.MULTILINE)
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise PatchPlanError(
            f"Model response is not valid JSON: {exc}\nRaw (first 400 chars): {raw[:400]}"
        ) from exc

    if not isinstance(data, dict):
        raise PatchPlanError(
            f"Plan JSON must be an object, got {type(data).__name__}"
        )

    # Ensure taskId is present
    data.setdefault("taskId", task_id)

    operations: Any = data.get("operations")
    if not isinstance(operations, list):
        raise PatchPlanError(
            "'operations' must be a JSON array"
        )

    if len(operations) == 0:
        raise PatchPlanError(
            f"Plan for {task_id!r} has no operations — model returned an empty plan"
        )

    for i, op in enumerate(operations):
        if not isinstance(op, dict):
            raise PatchPlanError(f"operations[{i}] must be a JSON object")

        action = op.get("action")
        if action not in _VALID_ACTIONS:
            raise PatchPlanError(
                f"operations[{i}] has unknown action {action!r}. "
                f"Valid actions: {sorted(_VALID_ACTIONS)}"
            )

        if action in ("replace", "insert_after"):
            if not op.get("search"):
                raise PatchPlanError(
                    f"operations[{i}] action={action!r} is missing 'search' field"
                )

        if action == "create":
            if "content" not in op:
                raise PatchPlanError(
                    f"operations[{i}] action='create' is missing 'content' field"
                )

    return data


# ── Helpers ────────────────────────────────────────────────────────────────


def _load_context(affected_paths: list[str], repo_root: Path) -> dict[str, str]:
    """Load the content of each affected file, skipping missing/oversized ones."""
    real_root = os.path.realpath(repo_root)
    context: dict[str, str] = {}

    for rel in affected_paths:
        resolved = os.path.realpath(Path(real_root) / rel)
        if not resolved.startswith(real_root):
            logger.warning("patch_planner: rejecting path outside repo: %r", rel)
            continue
        abs_path = Path(resolved)
        if not abs_path.is_file():
            logger.warning("patch_planner: file not found: %r", rel)
            continue
        if abs_path.stat().st_size > _MAX_FILE_BYTES:
            logger.warning("patch_planner: skipping oversized file: %r", rel)
            continue
        try:
            context[rel] = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("patch_planner: cannot read %r: %s", rel, exc)

    return context


def _build_prompt(
    issue: dict[str, Any],
    context_files: dict[str, str],
    pipeline_id: str,
) -> str:
    """Build the LLM user prompt from the issue and file context."""
    lines: list[str] = [
        f"taskId: {issue.get('taskId', '')}",
        f"pipeline: {pipeline_id}",
        f"title: {issue.get('title', '')}",
        f"description: {issue.get('description', '')}",
        f"severity: {issue.get('severity', '')}",
        f"requiredOutcome: {issue.get('requiredOutcome', '')}",
        f"allowedFixClass: {issue.get('allowedFixClass', '')}",
        f"affectedPaths: {issue.get('affectedPaths', [])}",
        f"evidence: {issue.get('evidence', [])}",
        "",
        "File contents:",
    ]
    for path, content in context_files.items():
        lines.append(f"--- {path} ---")
        lines.append(content)
        lines.append("")

    return "\n".join(lines)
