"""
Issue normaliser for the Repo Management Suite.

Converts raw audit findings into NormalisedIssue dicts, applying:
  - Classification logic (code_fix / future_guidance / manual_review)
  - Editorial guard for on-brand blog/transcript findings (_is_editorial)
  - Protected-path gate for mobile-ux (blog/posts/, transcripts/, etc.)
  - Task-ID generation
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

from repo_mgmt.patch_protocol import is_protected

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId, Settings
    from repo_mgmt.model_router import ModelRouter

logger = logging.getLogger(__name__)

# Mobile-ux protected prefixes — normaliser gate
_MOBILE_UX_PROTECTED: frozenset[str] = frozenset(
    [
        "blog/posts/",
        "blog/posts.json",
        "transcripts/",
        "data/podcast-episodes.json",
        "assets/js/podcast-transcripts.min.js",
        "functions/transcripts/",
    ]
)

# Heuristic editorial signal pattern
_EDITORIAL_RE = re.compile(
    r"\b(tone|voice|punchiness|brand voice|wording|rewrite|rephrase|"
    r"quality|style|copy|messaging|engaging|compelling|punchy|vivid|"
    r"historical content|dated content|older post)\b",
    re.IGNORECASE,
)

# On-brand: allowed code_fix sub-classes for blog/transcript paths
_ONBRAND_STRUCTURAL_CLASSES = frozenset(
    ["html_fix", "template_fix", "schema_fix", "meta_fix", "partial_fix"]
)

# Approved fix classes per pipeline
_APPROVED_FIX_CLASSES: dict[str, frozenset[str]] = {
    "seo-aeo-geo": frozenset(
        ["route_fix", "config_fix", "schema_fix", "prompt_template_update",
         "audit_output_fix", "middleware_fix"]
    ),
    "mobile-ux": frozenset(
        ["html_fix", "css_fix", "meta_fix", "viewport_fix", "accessibility_fix",
         "template_fix", "partial_fix"]
    ),
    "on-brand": frozenset(
        ["html_fix", "template_fix", "schema_fix", "meta_fix", "partial_fix"]
    ),
}

# Blog/transcript path prefixes for on-brand editorial guard
_ONBRAND_CONTENT_PREFIXES = ("blog/", "transcripts/")


def _is_editorial(finding: dict[str, Any]) -> bool:
    """
    Return True if *finding* is an editorial issue.

    Editorial issues include: tone, voice, punchiness, brand wording quality,
    historical content critique, or any rewrite/rephrasing suggestion.

    These must never reach patch_applier.py — they become future_guidance.

    Args:
        finding: Raw finding dict from the audit snapshot.

    Returns:
        True if the finding is editorial in nature.
    """
    title = str(finding.get("title", ""))
    description = str(finding.get("description", ""))
    category = str(finding.get("category", ""))
    combined = f"{title} {description} {category}"
    return bool(_EDITORIAL_RE.search(combined))


def _is_blog_or_transcript_path(paths: list[str]) -> bool:
    """Return True if any affected path is under blog/ or transcripts/."""
    for p in paths:
        if any(p.startswith(prefix) for prefix in _ONBRAND_CONTENT_PREFIXES):
            return True
    return False


def normalise(
    raw_findings: list[dict[str, Any]],
    pipeline_id: "PipelineId",
    cfg: "Settings",
    model_router: "ModelRouter",
) -> list[dict[str, Any]]:
    """
    Convert *raw_findings* into a list of NormalisedIssue dicts.

    Args:
        raw_findings: List of raw finding dicts from the audit JSON.
        pipeline_id: Active pipeline identifier.
        cfg: Validated RMS settings.
        model_router: ModelRouter for ambiguous triage calls.

    Returns:
        List of NormalisedIssue dicts with keys:
          taskId, title, description, severity, confidence,
          classification, affectedPaths, fixClass.
    """
    approved = _APPROVED_FIX_CLASSES.get(pipeline_id, frozenset())
    results: list[dict[str, Any]] = []

    for finding in raw_findings:
        affected_paths: list[str] = finding.get("affectedPaths", [])
        fix_class: str = str(finding.get("fixClass", ""))
        severity: str = str(finding.get("severity", "low"))
        confidence: float = float(finding.get("confidence", 1.0))
        classification: str

        # ── Mobile-ux protected path gate ─────────────────────────────────
        if pipeline_id == "mobile-ux":
            if any(is_protected(p, _MOBILE_UX_PROTECTED) for p in affected_paths):
                classification = "future_guidance"
                results.append(_build(finding, classification, affected_paths, fix_class))
                continue

        # ── On-brand editorial guard ───────────────────────────────────────
        if pipeline_id == "on-brand" and _is_blog_or_transcript_path(affected_paths):
            if _is_editorial(finding):
                classification = "future_guidance"
                results.append(_build(finding, classification, affected_paths, fix_class))
                continue
            # For ambiguous cases, ask triage model
            if _is_ambiguous_for_triage(finding):
                editorial = _triage_editorial(finding, model_router, cfg)
                if editorial:
                    classification = "future_guidance"
                    results.append(_build(finding, classification, affected_paths, fix_class))
                    continue
            # Structural/metadata defects are code_fix only for allowed classes
            if fix_class not in _ONBRAND_STRUCTURAL_CLASSES:
                classification = "future_guidance"
                results.append(_build(finding, classification, affected_paths, fix_class))
                continue

        # ── General classification ─────────────────────────────────────────
        explicit = str(finding.get("classification", ""))
        if explicit in ("future_guidance", "manual_review"):
            classification = explicit
        elif fix_class in approved:
            classification = "code_fix"
        elif fix_class:
            classification = "manual_review"
        else:
            classification = "future_guidance"

        results.append(_build(finding, classification, affected_paths, fix_class))

    return results


def _build(
    finding: dict[str, Any],
    classification: str,
    affected_paths: list[str],
    fix_class: str,
) -> dict[str, Any]:
    """Build a NormalisedIssue dict from a raw finding."""
    return {
        "taskId": finding.get("taskId") or f"rms-task-{uuid.uuid4().hex[:8]}",
        "title": finding.get("title", ""),
        "description": finding.get("description", ""),
        "severity": finding.get("severity", "low"),
        "confidence": float(finding.get("confidence", 1.0)),
        "classification": classification,
        "affectedPaths": affected_paths,
        "fixClass": fix_class,
        "requiredOutcome": finding.get("requiredOutcome", ""),
    }


def _is_ambiguous_for_triage(finding: dict[str, Any]) -> bool:
    """Return True if the finding may need model triage to classify."""
    title = str(finding.get("title", ""))
    description = str(finding.get("description", ""))
    # Ambiguous if it mentions content quality but also structural keywords
    structural = re.search(
        r"\b(schema|meta|template|partial|html|tag|attribute|markup)\b",
        f"{title} {description}",
        re.IGNORECASE,
    )
    editorial = _EDITORIAL_RE.search(f"{title} {description}")
    return bool(structural and editorial)


def _triage_editorial(
    finding: dict[str, Any],
    model_router: "ModelRouter",
    cfg: "Settings",
) -> bool:
    """
    Ask the triage model whether *finding* is editorial.

    Uses OPENROUTER_TRIAGE_MODEL only.  Returns True if editorial.
    Falls back to True (safer — avoid spurious code_fix) on any error.
    """
    import json as _json
    prompt = (
        "Classify the following repository audit finding.\n"
        "Reply ONLY with JSON: {\"editorial\": true} or {\"editorial\": false}\n\n"
        f"Title: {finding.get('title','')}\n"
        f"Description: {finding.get('description','')}\n"
        f"Category: {finding.get('category','')}\n"
    )
    try:
        raw = model_router.triage(prompt)
        data = _json.loads(raw)
        return bool(data.get("editorial", True))
    except Exception as exc:
        logger.warning("issue_normaliser: triage call failed (%s) — treating as editorial", exc)
        return True
