"""AnchorPatch/v1 patch planner for RAMS."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repo_mgmt import context_builder
from repo_mgmt.patch_applier import PROTECTED_PATHS
from repo_mgmt.patch_protocol import (
    PatchSchemaError,
    is_protected,
    validate_patch,
)

if TYPE_CHECKING:
    from repo_mgmt.config import Settings
    from repo_mgmt.model_router import ModelRouter

logger = logging.getLogger(__name__)
_FENCED_JSON_RE = re.compile(
    r"^```(?:json)?\s*(?P<body>.*?)\s*```$", re.DOTALL | re.IGNORECASE
)


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

AnchorPatch/v1 exact schema:
{
  "patchProtocol": "AnchorPatch/v1",
  "changes": [
    {
      "file": "repo-relative/path.ext",
      "operation": "replace" | "insert_after" | "delete",
      "anchorBefore": "exact unique anchor text from the current file",
      "find": "exact unique text to replace, insert after, or delete",
      "replace": "replacement or insertion text",
      "rationale": "brief reason for this bounded change"
    }
  ],
  "reason": "required only when changes is empty"
}

Rules:
1. Only include files listed in affectedPaths.
2. Use the exact key names above: file, operation, anchorBefore, find, replace, rationale.
3. Do not use aliases such as path, action, before, search, replacement, or explanation.
4. anchorBefore must appear exactly once in the current file.
5. find must appear exactly once in the current file.
6. Do not rename files, add new dependencies, or change unrelated code.
7. Do not touch any file whose path matches a protected prefix
(blog/posts/, transcripts/, data/podcast-episodes.json) unless
this pipeline explicitly has authority over those paths.
8. If the required outcome cannot be safely achieved with a bounded
patch, return:
{"patchProtocol":"AnchorPatch/v1","changes":[],"reason":"<why>"}
"""

REPAIR_SYSTEM_PROMPT = """
You repair invalid repository patch-planner output.
Return exactly one valid JSON object conforming to AnchorPatch/v1.
Do not include prose, markdown fences, analysis, comments, or any text
outside the JSON object.

Every change must use these exact keys only:
file, operation, anchorBefore, find, replace, rationale.
Never use aliases such as path, action, before, search, replacement,
content, text, explanation, or reason inside a change object.
If a safe bounded patch cannot be produced, return
{"patchProtocol":"AnchorPatch/v1","changes":[],"reason":"<why>"}.
"""


def plan(
    issue: dict[str, Any],
    target_repo: Path,
    pipeline_id: str,
    settings: "Settings",
    model_router: "ModelRouter",
) -> dict[str, Any]:
    """Request and validate a strict AnchorPatch/v1 plan synchronously."""
    task_id, affected_paths, prompt = _prepare_plan_inputs(
        issue, target_repo, pipeline_id, settings
    )
    raw = _complete_for_plan(model_router, prompt, SYSTEM_PROMPT)
    try:
        patch_doc = _parse_plan(raw, task_id)
    except PatchPlanError as first_err:
        logger.warning(
            "patch_planner [%s]: invalid planner JSON; retrying once with repair prompt: %s",
            task_id,
            first_err,
        )
        repair_prompt = _build_repair_prompt(prompt, raw, str(first_err))
        repaired = _complete_for_plan(model_router, repair_prompt, REPAIR_SYSTEM_PROMPT)
        try:
            patch_doc = _parse_plan(repaired, task_id)
        except PatchPlanError as second_err:
            raise PatchPlanError(
                f"Planner returned invalid AnchorPatch/v1 after repair retry: {second_err}"
            ) from second_err
    _validate_plan_scope(patch_doc, affected_paths, pipeline_id)
    return patch_doc


async def plan_async(
    issue: dict[str, Any],
    target_repo: Path,
    pipeline_id: str,
    settings: "Settings",
    model_router: "ModelRouter",
) -> dict[str, Any]:
    """Request and validate a strict AnchorPatch/v1 plan without blocking the event loop."""
    task_id, affected_paths, prompt = _prepare_plan_inputs(
        issue, target_repo, pipeline_id, settings
    )
    raw = await _complete_for_plan_async(model_router, prompt, SYSTEM_PROMPT)
    try:
        patch_doc = _parse_plan(raw, task_id)
    except PatchPlanError as first_err:
        logger.warning(
            "patch_planner [%s]: invalid planner JSON; retrying once with repair prompt: %s",
            task_id,
            first_err,
        )
        repair_prompt = _build_repair_prompt(prompt, raw, str(first_err))
        repaired = await _complete_for_plan_async(
            model_router, repair_prompt, REPAIR_SYSTEM_PROMPT
        )
        try:
            patch_doc = _parse_plan(repaired, task_id)
        except PatchPlanError as second_err:
            raise PatchPlanError(
                f"Planner returned invalid AnchorPatch/v1 after repair retry: {second_err}"
            ) from second_err
    _validate_plan_scope(patch_doc, affected_paths, pipeline_id)
    return patch_doc


def _complete_for_plan(model_router: "ModelRouter", prompt: str, system: str) -> str:
    """Call a router for patch planning, requesting JSON mode when supported."""
    try:
        return str(
            model_router.complete(
                prompt=prompt,
                system=system,
                max_tokens=4096,
                json_mode=True,
            )
        )
    except TypeError:
        # Older tests or compatible routers may not yet expose json_mode.
        try:
            return str(
                model_router.complete(prompt=prompt, system=system, max_tokens=4096)
            )
        except Exception as exc:
            raise PatchPlanError(f"LLM call failed: {exc}") from exc
    except Exception as exc:
        raise PatchPlanError(f"LLM call failed: {exc}") from exc


async def _complete_for_plan_async(
    model_router: "ModelRouter", prompt: str, system: str
) -> str:
    """Async variant of _complete_for_plan with a sync-router fallback."""
    try:
        complete_async = getattr(model_router, "complete_async", None)
        if inspect.iscoroutinefunction(complete_async):
            return str(
                await complete_async(
                    prompt=prompt,
                    system=system,
                    max_tokens=4096,
                    json_mode=True,
                )
            )
        return str(
            await asyncio.to_thread(
                model_router.complete,
                prompt=prompt,
                system=system,
                max_tokens=4096,
                json_mode=True,
            )
        )
    except TypeError:
        try:
            complete_async = getattr(model_router, "complete_async", None)
            if inspect.iscoroutinefunction(complete_async):
                return str(
                    await complete_async(prompt=prompt, system=system, max_tokens=4096)
                )
            return str(
                await asyncio.to_thread(
                    model_router.complete,
                    prompt=prompt,
                    system=system,
                    max_tokens=4096,
                )
            )
        except Exception as exc:
            raise PatchPlanError(f"LLM call failed: {exc}") from exc
    except Exception as exc:
        raise PatchPlanError(f"LLM call failed: {exc}") from exc


def _build_repair_prompt(original_prompt: str, raw: str, error: str) -> str:
    """Build a bounded repair prompt after invalid planner output."""
    return json.dumps(
        {
            "repairTask": "Return only one valid AnchorPatch/v1 JSON object.",
            "parseError": error[:1000],
            "previousResponse": raw[:3000],
            "originalRequest": json.loads(original_prompt),
            "outputContract": {
                "patchProtocol": "AnchorPatch/v1",
                "changes": [],
                "reason": "<why if no bounded patch is safe>",
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def _prepare_plan_inputs(
    issue: dict[str, Any],
    target_repo: Path,
    pipeline_id: str,
    settings: "Settings",
) -> tuple[str, list[str], str]:
    """Build task id, affected path list, and model prompt for patch planning."""
    task_id = str(issue.get("taskId", "<unknown>"))
    if issue.get("classification") != "code_fix":
        raise PatchPlanError(
            f"plan() called on non-code_fix issue (classification={issue.get('classification')!r})"
        )
    affected_paths = [str(path) for path in issue.get("affectedPaths", [])]
    context_files = context_builder.load_context(
        affected_paths,
        target_repo,
        max_files=settings.rms_max_context_files,
        max_file_bytes=settings.rms_max_context_file_bytes,
        max_total_bytes=settings.rms_max_context_total_bytes,
    )
    return task_id, affected_paths, _build_prompt(issue, context_files, pipeline_id)


def _parse_plan(raw: str, task_id: str) -> dict[str, Any]:
    """Parse a model response as JSON and validate AnchorPatch/v1."""
    text = raw.strip()
    fenced = _FENCED_JSON_RE.match(text)
    if fenced:
        text = fenced.group("body").strip()
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
    data = _canonicalise_anchor_patch(data, task_id)
    try:
        return validate_patch(data)
    except PatchSchemaError as exc:
        raise PatchPlanError(
            f"Invalid AnchorPatch/v1 plan for {task_id!r}: {exc}"
        ) from exc


def _canonicalise_anchor_patch(data: Any, task_id: str) -> Any:
    """Normalise common model key aliases into AnchorPatch/v1.

    Some providers still return near-miss JSON after the repair prompt, for
    example ``path`` instead of ``file`` or ``action`` instead of
    ``operation``. This adapter is intentionally narrow: it never fabricates
    anchors, find strings, replacement text, or files. It only translates
    obvious synonyms and supplies a missing rationale so the strict schema can
    make the final safety decision.
    """
    if not isinstance(data, dict):
        return data
    if data.get("patchProtocol") != "AnchorPatch/v1":
        protocol = data.get("protocol") or data.get("patch_protocol")
        if protocol == "AnchorPatch/v1":
            data = {**data, "patchProtocol": "AnchorPatch/v1"}
    changes = data.get("changes")
    if not isinstance(changes, list):
        return data
    canonical_changes: list[Any] = []
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            canonical_changes.append(change)
            continue
        canonical = dict(change)
        _copy_first_present(
            canonical, "file", ["path", "filePath", "repoPath", "filename"]
        )
        _copy_first_present(canonical, "operation", ["action", "op", "type"])
        _copy_first_present(
            canonical,
            "anchorBefore",
            ["anchor", "before", "anchorText", "anchor_before", "context", "location"],
        )
        _copy_first_present(
            canonical,
            "find",
            ["search", "findText", "oldText", "old", "target", "match", "current"],
        )
        _copy_first_present(
            canonical,
            "replace",
            [
                "replacement",
                "replaceWith",
                "newText",
                "new",
                "insert",
                "content",
                "text",
                "updated",
            ],
        )
        _copy_first_present(canonical, "rationale", ["explanation", "reason", "why"])

        if "operation" in canonical:
            canonical["operation"] = _normalise_operation(canonical.get("operation"))
        elif canonical.get("find") and "replace" in canonical:
            canonical["operation"] = "replace"

        if (
            not canonical.get("rationale")
            and canonical.get("file")
            and canonical.get("operation")
        ):
            canonical["rationale"] = f"Bounded patch proposed for {task_id}."

        for alias in (
            "path",
            "filePath",
            "repoPath",
            "filename",
            "action",
            "op",
            "type",
            "anchor",
            "before",
            "anchorText",
            "anchor_before",
            "context",
            "location",
            "search",
            "findText",
            "oldText",
            "old",
            "target",
            "match",
            "current",
            "replacement",
            "replaceWith",
            "newText",
            "new",
            "insert",
            "content",
            "text",
            "updated",
            "explanation",
            "why",
        ):
            canonical.pop(alias, None)
        canonical_changes.append(canonical)
    return {**data, "changes": canonical_changes}


def _copy_first_present(
    target: dict[str, Any], canonical_key: str, aliases: list[str]
) -> None:
    """Copy the first non-empty alias into *canonical_key* when absent."""
    if target.get(canonical_key) not in (None, ""):
        return
    for alias in aliases:
        value = target.get(alias)
        if value not in (None, ""):
            target[canonical_key] = value
            return


def _normalise_operation(value: Any) -> Any:
    """Map common operation aliases onto the strict operation enum."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    mapping = {
        "replace": "replace",
        "update": "replace",
        "substitute": "replace",
        "insert_after": "insert_after",
        "insertafter": "insert_after",
        "append_after": "insert_after",
        "add_after": "insert_after",
        "insert": "insert_after",
        "append": "insert_after",
        "delete": "delete",
        "remove": "delete",
    }
    return mapping.get(text, value)


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
