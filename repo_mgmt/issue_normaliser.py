"""
Issue normaliser for the Repo Management Suite.

Converts raw audit findings into NormalisedIssue dicts, applying:
  - Exact NormalisedIssue schema (all required fields)
  - Deterministic task IDs: rms-<pipeline>-<YYYY-MM-DD>-<seq:003d>
  - Classification logic (code_fix / future_guidance / manual_review / skipped)
  - Editorial guard for on-brand blog/transcript findings
  - Protected-path gate for mobile-ux (-> skipped_not_actionable)
  - Approved fix-class enforcement per pipeline

API:
  normalise(audit, pipeline_id, run_date, cfg, model_router=None) -> list[dict]

  audit:        full audit dict with a "findings" key (empty dict returns [])
  pipeline_id:  "seo-aeo-geo" | "mobile-ux" | "on-brand"
  run_date:     "YYYY-MM-DD" string used in deterministic task IDs
  cfg:          Settings — provides validation_commands_for()
  model_router: optional — used only for ambiguous on-brand triage
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from repo_mgmt.patch_protocol import is_protected

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId, Settings
    from repo_mgmt.model_router import ModelRouter

logger = logging.getLogger(__name__)

# ── Protected prefixes — normaliser-layer gate for mobile-ux ──────────────

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

# ── Approved fix classes per pipeline ─────────────────────────────────────

_APPROVED_FIX_CLASSES: dict[str, frozenset[str]] = {
    "seo-aeo-geo": frozenset(
        [
            "route_fix", "config_fix", "schema_fix",
            "prompt_template_update", "audit_output_fix", "middleware_fix",
        ]
    ),
    "mobile-ux": frozenset(
        [
            "html_fix", "css_fix", "meta_fix", "viewport_fix",
            "accessibility_fix", "redirect_fix",
        ]
    ),
    "on-brand": frozenset(
        [
            "html_fix", "css_fix", "template_fix", "partial_fix",
            "redirect_fix", "prompt_template_update", "schema_fix", "meta_fix",
        ]
    ),
}

# ── On-brand editorial detection ──────────────────────────────────────────

# Heuristic: match clear editorial / quality-critique keywords
_EDITORIAL_RE = re.compile(
    r"\b(tone|voice|punchiness|brand voice|wording|rewrite|rephrase|"
    r"quality|style|copy|messaging|engaging|compelling|punchy|vivid|"
    r"historical content|dated content|older post)\b",
    re.IGNORECASE,
)

# On-brand: allowed code_fix classes for blog/transcript structural defects
_ONBRAND_STRUCTURAL_CLASSES: frozenset[str] = frozenset(
    ["html_fix", "template_fix", "schema_fix", "meta_fix", "partial_fix"]
)

# Blog/transcript path prefixes — on-brand editorial guard scope
_ONBRAND_CONTENT_PREFIXES = ("blog/", "transcripts/")


# ── Public API ─────────────────────────────────────────────────────────────


def normalise(
    audit: dict[str, Any],
    pipeline_id: "PipelineId",
    run_date: str,
    cfg: "Settings",
    model_router: "ModelRouter | None" = None,
) -> list[dict[str, Any]]:
    """
    Convert audit findings into a list of NormalisedIssue dicts.

    Args:
        audit: Full audit snapshot dict. Findings are taken from audit["findings"].
               Empty dict or missing "findings" key returns [].
        pipeline_id: Active pipeline identifier.
        run_date: "YYYY-MM-DD" string — embedded in deterministic task IDs.
        cfg: Validated RMS settings (provides validation_commands_for).
        model_router: Optional ModelRouter for ambiguous on-brand triage.

    Returns:
        List of NormalisedIssue dicts with all required schema fields.
    """
    raw_findings: list[dict[str, Any]] = audit.get("findings", [])
    if not raw_findings:
        return []

    approved = _APPROVED_FIX_CLASSES.get(pipeline_id, frozenset())
    validation_commands = cfg.validation_commands_for(pipeline_id)
    results: list[dict[str, Any]] = []
    seq = 0  # 1-based sequential counter per normalise() call

    for finding in raw_findings:
        seq += 1
        task_id = f"rms-{pipeline_id}-{run_date}-{seq:03d}"
        affected_paths: list[str] = finding.get("affectedPaths", [])
        fix_class: str = str(finding.get("fixClass", ""))
        severity: str = str(finding.get("severity", "low")).lower()
        confidence: float = float(finding.get("confidence", 1.0))
        evidence: list[str] = finding.get("evidence", [])
        source_audit: str = str(finding.get("sourceAudit", pipeline_id))
        required_outcome: str = str(finding.get("requiredOutcome", ""))

        # ── Mobile-ux protected path gate (normaliser layer) ──────────────
        if pipeline_id == "mobile-ux":
            if any(is_protected(p, _MOBILE_UX_PROTECTED) for p in affected_paths):
                results.append(
                    _build(
                        task_id=task_id,
                        pipeline_id=pipeline_id,
                        finding=finding,
                        classification="skipped",
                        status="skipped_not_actionable",
                        affected_paths=affected_paths,
                        fix_class=fix_class,
                        severity=severity,
                        confidence=confidence,
                        evidence=evidence,
                        source_audit=source_audit,
                        required_outcome=required_outcome,
                        allowed_fix_class=fix_class,
                        validation_commands=validation_commands,
                    )
                )
                continue

        # ── On-brand editorial guard ───────────────────────────────────────
        if pipeline_id == "on-brand" and _is_blog_or_transcript_path(affected_paths):
            if _is_editorial(finding):
                results.append(
                    _build(
                        task_id=task_id,
                        pipeline_id=pipeline_id,
                        finding=finding,
                        classification="future_guidance",
                        status="future_guidance",
                        affected_paths=affected_paths,
                        fix_class=fix_class,
                        severity=severity,
                        confidence=confidence,
                        evidence=evidence,
                        source_audit=source_audit,
                        required_outcome=required_outcome,
                        allowed_fix_class=fix_class,
                        validation_commands=validation_commands,
                    )
                )
                continue

            # For genuinely ambiguous cases, ask triage model (if available)
            if model_router is not None and _is_ambiguous_for_triage(finding):
                editorial = _triage_editorial(finding, model_router)
                if editorial:
                    results.append(
                        _build(
                            task_id=task_id,
                            pipeline_id=pipeline_id,
                            finding=finding,
                            classification="future_guidance",
                            status="future_guidance",
                            affected_paths=affected_paths,
                            fix_class=fix_class,
                            severity=severity,
                            confidence=confidence,
                            evidence=evidence,
                            source_audit=source_audit,
                            required_outcome=required_outcome,
                            allowed_fix_class=fix_class,
                            validation_commands=validation_commands,
                        )
                    )
                    continue

            # Structural/metadata: allowed only for specific structural classes
            if fix_class not in _ONBRAND_STRUCTURAL_CLASSES:
                results.append(
                    _build(
                        task_id=task_id,
                        pipeline_id=pipeline_id,
                        finding=finding,
                        classification="future_guidance",
                        status="future_guidance",
                        affected_paths=affected_paths,
                        fix_class=fix_class,
                        severity=severity,
                        confidence=confidence,
                        evidence=evidence,
                        source_audit=source_audit,
                        required_outcome=required_outcome,
                        allowed_fix_class=fix_class,
                        validation_commands=validation_commands,
                    )
                )
                continue

        # ── General classification ─────────────────────────────────────────
        explicit = str(finding.get("classification", ""))
        if explicit in ("future_guidance", "manual_review", "skipped"):
            classification = explicit
            status = explicit
        elif fix_class == "future_guidance":
            classification = "future_guidance"
            status = "future_guidance"
        elif fix_class in approved:
            classification = "code_fix"
            status = "pending"
        elif fix_class:
            classification = "manual_review"
            status = "manual_review"
        else:
            classification = "future_guidance"
            status = "future_guidance"

        results.append(
            _build(
                task_id=task_id,
                pipeline_id=pipeline_id,
                finding=finding,
                classification=classification,
                status=status,
                affected_paths=affected_paths,
                fix_class=fix_class,
                severity=severity,
                confidence=confidence,
                evidence=evidence,
                source_audit=source_audit,
                required_outcome=required_outcome,
                allowed_fix_class=fix_class if classification == "code_fix" else "",
                validation_commands=validation_commands,
            )
        )

    return results


# ── Internal helpers ───────────────────────────────────────────────────────


def _build(
    *,
    task_id: str,
    pipeline_id: str,
    finding: dict[str, Any],
    classification: str,
    status: str,
    affected_paths: list[str],
    fix_class: str,
    severity: str,
    confidence: float,
    evidence: list[str],
    source_audit: str,
    required_outcome: str,
    allowed_fix_class: str,
    validation_commands: list[str],
) -> dict[str, Any]:
    """Construct a complete NormalisedIssue dict with all required schema fields."""
    return {
        "taskId": task_id,
        "pipeline": pipeline_id,
        "sourceAudit": source_audit,
        "classification": classification,
        "severity": severity,
        "confidence": confidence,
        "affectedPaths": affected_paths,
        "evidence": evidence,
        "requiredOutcome": required_outcome,
        "allowedFixClass": allowed_fix_class,
        "validationCommands": validation_commands,
        "status": status,
        # Carry through extra fields that patch_planner / report may use
        "title": finding.get("title", ""),
        "description": finding.get("description", ""),
        "fixClass": fix_class,
    }


def _is_editorial(finding: dict[str, Any]) -> bool:
    """
    Return True if *finding* is an editorial quality issue.

    Uses deterministic keyword/rule checks only.  Never calls a model.
    Model triage is done separately in _triage_editorial for genuinely
    ambiguous cases (structural + editorial signals).
    """
    title = str(finding.get("title", ""))
    description = str(finding.get("description", ""))
    category = str(finding.get("category", ""))
    combined = f"{title} {description} {category}"
    return bool(_EDITORIAL_RE.search(combined))


def _is_blog_or_transcript_path(paths: list[str]) -> bool:
    """Return True if any affected path is under blog/ or transcripts/."""
    return any(
        p.startswith(prefix) for p in paths for prefix in _ONBRAND_CONTENT_PREFIXES
    )


def _is_ambiguous_for_triage(finding: dict[str, Any]) -> bool:
    """Return True if the finding has both structural AND editorial signals."""
    text = f"{finding.get('title', '')} {finding.get('description', '')}"
    has_structural = bool(
        re.search(
            r"\b(schema|meta|template|partial|html|tag|attribute|markup)\b",
            text,
            re.IGNORECASE,
        )
    )
    has_editorial = bool(_EDITORIAL_RE.search(text))
    return has_structural and has_editorial


def _triage_editorial(
    finding: dict[str, Any],
    model_router: "ModelRouter",
) -> bool:
    """
    Ask the triage model whether *finding* is editorial.

    Uses OPENROUTER_TRIAGE_MODEL only — never the primary patch model.
    Falls back to True (treat as editorial, safer) on any error.
    """
    import json as _json

    prompt = (
        "Classify the following repository audit finding.\n"
        'Reply ONLY with JSON: {"editorial": true} or {"editorial": false}\n\n'
        f"Title: {finding.get('title', '')}\n"
        f"Description: {finding.get('description', '')}\n"
        f"Category: {finding.get('category', '')}\n"
    )
    try:
        raw = model_router.triage(prompt)
        data = _json.loads(raw)
        return bool(data.get("editorial", True))
    except Exception as exc:
        logger.warning(
            "issue_normaliser: triage call failed (%s) — treating as editorial", exc
        )
        return True
