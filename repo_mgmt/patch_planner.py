"""
Patch planner for the Repo Management Suite.

For each code_fix NormalisedIssue, sends a structured prompt to the primary
LLM model and receives a PatchPlan (list of file operations).

PatchPlan schema:
  {
    "taskId": "rms-on-brand-2026-05-05-001",
    "operations": [
      {
        "action": "replace" | "create" | "delete" | "insert_after",
        "path": "relative/path/in/repo",
        "search": "...",       # required for replace/insert_after
        "replacement": "...",  # required for replace/insert_after
        "content": "..."       # required for create
      }
    ]
  }

Raises PatchPlanError if the model response cannot be parsed into valid ops.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId, Settings
    from repo_mgmt.model_router import ModelRouter

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a precise, surgical code-fix engineer.
You receive a structured issue description and must respond with a JSON PatchPlan.

Rules:
1. Output ONLY valid JSON — no preamble, no markdown fences, no commentary.
2. Never modify editorial content (tone, voice, wording quality).
3. Only touch files listed in affectedPaths unless the fix logically requires one additional file.
4. Use the smallest possible change to achieve the requiredOutcome.
5. Do not create new tests, documentation, or change blank lines unnecessarily.

PatchPlan schema:
{
  "taskId": "<taskId from the issue>",
  "operations": [
    {
      "action": "replace",
      "path": "relative/path/in/repo",
      "search": "<exact text to find — must be unique in file>",
      "replacement": "<new text>"
    }
  ]
}

Allowed action values: replace | create | delete | insert_after
- replace: replaces the first occurrence of "search" with "replacement"
- create: creates a new file at "path" with "content"
- delete: deletes the file at "path" (no search/replacement needed)
- insert_after: inserts "replacement" immediately after "search"
"""

_VALID_ACTIONS = frozenset(["replace", "create", "delete", "insert_after"])


class PatchPlanError(Exception):
    """Raised when a patch plan cannot be generated or parsed."""


def plan(
    issue: dict[str, Any],
    repo_root: Path,
    pipeline_id: "PipelineId",
    cfg: "Settings",
    router: "ModelRouter",
) -> dict[str, Any]:
    """
    Ask the LLM to produce a PatchPlan for *issue*.

    Args:
        issue: NormalisedIssue dict (classification must be "code_fix").
        repo_root: Absolute path to the local repository clone.
        pipeline_id: Pipeline being processed.
        cfg: Validated RMS settings.
        router: ModelRouter for LLM calls.

    Returns:
        Parsed PatchPlan dict with at least one operation.

    Raises:
        PatchPlanError: If the model response is not valid JSON, missing
                        required fields, or produces zero operations.
    """
    if issue.get("classification") != "code_fix":
        raise PatchPlanError(
            f"plan() called on non-code_fix issue {issue.get('taskId')!r}"
        )

    file_snippets = _collect_snippets(issue["affectedPaths"], repo_root)

    user_prompt = json.dumps(
        {
            "taskId": issue["taskId"],
            "pipeline": pipeline_id,
            "severity": issue["severity"],
            "evidence": issue["evidence"],
            "affectedPaths": issue["affectedPaths"],
            "requiredOutcome": issue["requiredOutcome"],
            "allowedFixClass": issue["allowedFixClass"],
            "fileSnippets": file_snippets,
        },
        indent=2,
    )

    logger.info("patch_planner: requesting plan for %s", issue["taskId"])
    try:
        raw = router.complete(user_prompt, system=_SYSTEM_PROMPT, max_tokens=4096)
    except Exception as exc:
        raise PatchPlanError(
            f"LLM call failed for {issue['taskId']}: {exc}"
        ) from exc

    return _parse_plan(raw, expected_task_id=issue["taskId"])


def _collect_snippets(
    paths: list[str], repo_root: Path, max_lines: int = 200
) -> dict[str, str]:
    """
    Return a dict of path → first-N-lines for each affected file that exists.

    Missing files are included as an empty string so the model knows to create them.
    """
    snippets: dict[str, str] = {}
    for rel_path in paths:
        abs_path = repo_root / rel_path
        if abs_path.is_file():
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
                lines = text.splitlines()
                snippets[rel_path] = "\n".join(lines[:max_lines])
                if len(lines) > max_lines:
                    snippets[rel_path] += f"\n... [{len(lines) - max_lines} lines truncated]"
            except OSError as exc:
                logger.warning("patch_planner: could not read %s: %s", abs_path, exc)
                snippets[rel_path] = ""
        else:
            snippets[rel_path] = ""  # File doesn't exist yet — model may create it
    return snippets


def _parse_plan(raw: str, expected_task_id: str) -> dict[str, Any]:
    """
    Parse the raw LLM response string into a PatchPlan dict.

    Args:
        raw: Raw string from the LLM.
        expected_task_id: Expected taskId for validation.

    Returns:
        Validated PatchPlan dict.

    Raises:
        PatchPlanError: If parsing or validation fails.
    """
    # Strip markdown fences if the model disobeyed instructions
    clean = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    try:
        plan_dict: dict[str, Any] = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise PatchPlanError(
            f"LLM response for {expected_task_id!r} is not valid JSON: {exc}\n"
            f"Raw (first 500 chars): {raw[:500]}"
        ) from exc

    if not isinstance(plan_dict, dict):
        raise PatchPlanError(
            f"PatchPlan for {expected_task_id!r} is not a JSON object"
        )

    ops = plan_dict.get("operations")
    if not isinstance(ops, list) or len(ops) == 0:
        raise PatchPlanError(
            f"PatchPlan for {expected_task_id!r} has no operations"
        )

    for i, op in enumerate(ops):
        action = op.get("action", "")
        if action not in _VALID_ACTIONS:
            raise PatchPlanError(
                f"Operation {i} has unknown action {action!r} for task {expected_task_id!r}"
            )
        if not op.get("path"):
            raise PatchPlanError(
                f"Operation {i} missing 'path' for task {expected_task_id!r}"
            )
        if action in ("replace", "insert_after"):
            if not op.get("search"):
                raise PatchPlanError(
                    f"Operation {i} action={action!r} missing 'search' for task {expected_task_id!r}"
                )
        if action == "create" and op.get("content") is None:
            raise PatchPlanError(
                f"Operation {i} action='create' missing 'content' for task {expected_task_id!r}"
            )

    # Stamp the canonical taskId
    plan_dict["taskId"] = expected_task_id
    return plan_dict
