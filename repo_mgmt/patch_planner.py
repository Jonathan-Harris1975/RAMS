"""
Patch planner for the Repo Management Suite.

For each code_fix NormalisedIssue, builds a prompt using the file context
and calls the primary LLM to generate an AnchorPatch/v1 document.

System prompt instructs the model to:
  - Return only valid AnchorPatch/v1 JSON
  - Make the smallest safe bounded patch
  - Never add dependencies, never rename files
  - Never touch protected paths
  - Return empty changes when no safe patch is possible

Raises PatchPlanError if the model response cannot be parsed.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from repo_mgmt.patch_protocol import PatchSchemaError, validate_patch

if TYPE_CHECKING:
    from repo_mgmt.model_router import ModelRouter

logger = logging.getLogger(__name__)

# ── System prompt (embedded verbatim as module constant) ───────────────────

SYSTEM_PROMPT = """You are a precise, minimal-footprint code-fix engineer integrated into an autonomous repository management pipeline.

Your task: produce an AnchorPatch/v1 JSON document that resolves a single repository finding.

Output rules (STRICT):
1. Output ONLY valid JSON — absolutely no preamble, no markdown fences, no commentary, no trailing text.
2. The root object must have exactly two keys: "patchProtocol" and "changes".
3. "patchProtocol" must equal "AnchorPatch/v1".
4. Each change must contain: "file", "operation", "find", "anchorBefore", "replace", "rationale".
5. "operation" must be one of: "replace", "insert_after", "delete".
6. "find" must be an exact verbatim substring of the target file — unique within the file.
7. "anchorBefore" must be a short unique string that appears immediately before "find" in the file.
8. Make the smallest possible change to achieve the requiredOutcome.
9. Only include files listed in affectedPaths.
10. Never add, remove, or rename files. Never add dependencies.
11. Never touch protected paths.
12. If no safe, bounded, deterministic patch is possible, return: {"patchProtocol": "AnchorPatch/v1", "changes": [], "reason": "<explain why>"}
"""


class PatchPlanError(Exception):
    """Raised when the model response cannot be parsed into a valid AnchorPatch/v1 document."""


def plan(
    issue: dict[str, Any],
    context_files: dict[str, str],
    model_router: "ModelRouter",
) -> dict[str, Any]:
    """
    Generate an AnchorPatch/v1 document for *issue*.

    Args:
        issue: NormalisedIssue dict with classification='code_fix'.
        context_files: Dict mapping repo-relative path → file content.
        model_router: Initialised ModelRouter for LLM calls.

    Returns:
        Validated AnchorPatch/v1 dict.  'changes' may be empty when no
        safe patch can be made (reason key is populated in that case).

    Raises:
        PatchPlanError: If the model response cannot be parsed or validated.
    """
    user_prompt = _build_user_prompt(issue, context_files)

    try:
        raw = model_router.complete(
            prompt=user_prompt,
            system=SYSTEM_PROMPT,
            max_tokens=4096,
        )
    except Exception as exc:
        raise PatchPlanError(f"Model call failed: {exc}") from exc

    patch_doc = _parse_response(raw)

    if patch_doc.get("changes"):
        try:
            validate_patch(patch_doc)
        except PatchSchemaError as exc:
            raise PatchPlanError(f"Model returned invalid AnchorPatch/v1 schema: {exc}") from exc

    return patch_doc


def _build_user_prompt(
    issue: dict[str, Any],
    context_files: dict[str, str],
) -> str:
    """
    Build the user prompt from the issue and its file context.

    Args:
        issue: NormalisedIssue dict.
        context_files: Affected file contents.

    Returns:
        Formatted prompt string.
    """
    lines: list[str] = [
        f"taskId: {issue.get('taskId', '')}",
        f"title: {issue.get('title', '')}",
        f"description: {issue.get('description', '')}",
        f"severity: {issue.get('severity', '')}",
        f"requiredOutcome: {issue.get('requiredOutcome', '')}",
        f"affectedPaths: {issue.get('affectedPaths', [])}",
        "",
        "File contents:",
    ]
    for path, content in context_files.items():
        lines.append(f"--- {path} ---")
        lines.append(content)
        lines.append("")

    return "\n".join(lines)


def _parse_response(raw: str) -> dict[str, Any]:
    """
    Extract and parse JSON from the raw model response.

    Strips markdown fences if present.  Returns empty-changes doc on
    parse failure rather than raising, so the executor can record a
    clean 'skipped' status.

    Args:
        raw: Raw string from model_router.complete().

    Returns:
        Parsed dict (may have empty 'changes' list).

    Raises:
        PatchPlanError: If the JSON cannot be parsed at all.
    """
    # Strip markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned.strip(), flags=re.MULTILINE)
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise PatchPlanError(
            f"Model response is not valid JSON: {exc}\n"
            f"Raw response (first 500 chars): {raw[:500]}"
        ) from exc

    if not isinstance(data, dict):
        raise PatchPlanError(
            f"Model response JSON must be an object, got {type(data).__name__}"
        )

    return data
