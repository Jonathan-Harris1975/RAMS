"""
Issue normaliser for the Repo Management Suite.

Converts raw audit findings into strict NormalisedIssue dicts, applying:
  - NormalisedIssue schema validation
  - Deterministic task IDs: rms-<pipeline>-<YYYY-MM-DD>-<seq:003d>
  - Classification logic (code_fix / future_guidance / manual_review / skipped)
  - Editorial guard for on-brand blog/transcript findings
  - Protected-path gate for mobile-ux (excluded from executable output)
  - Approved fix-class enforcement per pipeline
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from repo_mgmt.patch_protocol import is_protected
from repo_mgmt.schemas import NormalisedIssueModel, normalise_repo_relative_path

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId, Settings
    from repo_mgmt.model_router import ModelRouter

logger = logging.getLogger(__name__)

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

_APPROVED_FIX_CLASSES: dict[str, frozenset[str]] = {
    "seo-aeo-geo": frozenset(
        [
            "html_fix",
            "css_fix",
            "meta_fix",
            "schema_fix",
            "structured_data_fix",
            "canonical_fix",
            "redirect_fix",
            "crawler_fix",
            "sitemap_fix",
            "robots_fix",
            "llms_fix",
            "accessibility_fix",
            "template_fix",
            "partial_fix",
        ]
    ),
    "mobile-ux": frozenset(
        [
            "html_fix",
            "css_fix",
            "meta_fix",
            "viewport_fix",
            "accessibility_fix",
            "redirect_fix",
        ]
    ),
    "on-brand": frozenset(
        [
            "route_fix",
            "config_fix",
            "schema_fix",
            "prompt_template_update",
            "audit_output_fix",
            "middleware_fix",
            "html_fix",
            "css_fix",
            "template_fix",
            "partial_fix",
            "redirect_fix",
            "meta_fix",
        ]
    ),
}

_VALID_SEVERITIES = frozenset(["critical", "high", "medium", "low"])
_VALID_CLASSIFICATIONS = frozenset(
    ["code_fix", "future_guidance", "manual_review", "skipped"]
)

_EDITORIAL_RE = re.compile(
    r"\b(tone|voice|punchy|punchier|rewrite|wording|copy|style|brand wording|"
    r"quality|compelling|snappier|more human|sounds|phrase|phrasing)\b",
    re.IGNORECASE,
)
_STRUCTURAL_RE = re.compile(
    r"\b(schema|metadata|meta|template|partial|html|tag|attribute|markup|"
    r"broken|malformed|missing required|incorrect metadata|canonical|"
    r"structured data)\b",
    re.IGNORECASE,
)
_ONBRAND_STRUCTURAL_CLASSES: frozenset[str] = frozenset(
    ["html_fix", "template_fix", "schema_fix", "meta_fix", "partial_fix"]
)
_ONBRAND_CONTENT_PREFIXES = ("blog/", "transcripts/")


def normalise(
    audit: dict[str, Any],
    pipeline_id: "PipelineId",
    run_date: str,
    cfg: "Settings",
    model_router: "ModelRouter | None" = None,
) -> list[dict[str, Any]]:
    """
    Convert audit findings into strict NormalisedIssue dictionaries.

    Invalid or unsafe finding data is converted to manual_review/skipped output
    rather than raising and aborting the pipeline.
    """
    raw_findings = audit.get("findings", [])
    if not isinstance(raw_findings, list) or not raw_findings:
        return []

    approved = _APPROVED_FIX_CLASSES.get(pipeline_id, frozenset())
    validation_commands = cfg.validation_commands_for(pipeline_id)
    results: list[dict[str, Any]] = []

    for seq, raw_finding in enumerate(raw_findings, start=1):
        finding = raw_finding if isinstance(raw_finding, dict) else {}
        metadata_errors: list[str] = []
        if not isinstance(raw_finding, dict):
            metadata_errors.append("finding is not a JSON object")

        affected_paths, path_errors = _safe_affected_paths(finding.get("affectedPaths", []))
        metadata_errors.extend(path_errors)
        fix_class = str(finding.get("fixClass", "")).strip()
        severity, severity_error = _safe_severity(finding.get("severity", "low"))
        if severity_error:
            metadata_errors.append(severity_error)
        confidence, confidence_note = _safe_confidence(finding.get("confidence", 1.0))
        if confidence_note:
            metadata_errors.append(confidence_note)
        evidence = _safe_string_list(finding.get("evidence", [])) + metadata_errors
        source_audit = str(finding.get("sourceAudit", pipeline_id))
        required_outcome = str(finding.get("requiredOutcome", ""))
        task_id = f"rms-{pipeline_id}-{run_date}-{seq:03d}"

        if path_errors:
            skipped = _build_validated(
                task_id=task_id,
                pipeline_id=pipeline_id,
                finding=finding,
                classification="skipped",
                status="skipped_not_actionable",
                affected_paths=[],
                fix_class=fix_class,
                severity=severity,
                confidence=confidence,
                evidence=evidence,
                source_audit=source_audit,
                required_outcome=required_outcome,
                allowed_fix_class="",
                validation_commands=validation_commands,
            )
            skipped["skipReason"] = "; ".join(path_errors)
            results.append(skipped)
            continue

        if metadata_errors:
            results.append(
                _build_validated(
                    task_id=task_id,
                    pipeline_id=pipeline_id,
                    finding=finding,
                    classification="manual_review",
                    status="manual_review",
                    affected_paths=affected_paths,
                    fix_class=fix_class,
                    severity=severity,
                    confidence=confidence,
                    evidence=evidence,
                    source_audit=source_audit,
                    required_outcome=required_outcome,
                    allowed_fix_class="",
                    validation_commands=validation_commands,
                )
            )
            continue

        if pipeline_id == "mobile-ux" and any(
            is_protected(path, _MOBILE_UX_PROTECTED) for path in affected_paths
        ):
            reason = "mobile-ux finding targets protected content path; skipped"
            logger.info(
                "issue_normaliser: skipped protected mobile-ux finding paths=%s",
                affected_paths,
            )
            skipped = _build_validated(
                task_id=task_id,
                pipeline_id=pipeline_id,
                finding=finding,
                classification="skipped",
                status="skipped_not_actionable",
                affected_paths=affected_paths,
                fix_class=fix_class,
                severity=severity,
                confidence=confidence,
                evidence=evidence + [reason],
                source_audit=source_audit,
                required_outcome=required_outcome,
                allowed_fix_class="",
                validation_commands=validation_commands,
            )
            skipped["skipReason"] = reason
            results.append(skipped)
            continue

        if pipeline_id == "on-brand" and _is_blog_or_transcript_path(affected_paths):
            if _is_editorial(finding, model_router):
                results.append(
                    _build_validated(
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
            if fix_class not in _ONBRAND_STRUCTURAL_CLASSES:
                results.append(
                    _build_validated(
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

        explicit = str(finding.get("classification", "")).strip()
        if explicit in _VALID_CLASSIFICATIONS - {"code_fix"}:
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
            _build_validated(
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


def _safe_affected_paths(value: Any) -> tuple[list[str], list[str]]:
    """Return validated repo-relative paths plus any safety errors."""
    if value in (None, ""):
        return [], []
    if not isinstance(value, list):
        return [], ["affectedPaths must be a list of repo-relative paths"]
    paths: list[str] = []
    errors: list[str] = []
    for item in value:
        if not isinstance(item, str):
            errors.append(f"affectedPaths entry is not a string: {item!r}")
            continue
        try:
            paths.append(normalise_repo_relative_path(item))
        except ValueError as exc:
            errors.append(str(exc))
    return paths, errors


def _safe_string_list(value: Any) -> list[str]:
    """Return a list of string evidence items from a potentially messy value."""
    if isinstance(value, list):
        return [str(item) for item in value]
    if value in (None, ""):
        return []
    return [str(value)]


def _safe_severity(value: Any) -> tuple[str, str | None]:
    """Return a schema-valid severity and an error message when downgraded."""
    severity = str(value).strip().lower()
    if severity in _VALID_SEVERITIES:
        return severity, None
    return "low", f"invalid severity {value!r}; routed to manual_review"


def _safe_confidence(value: Any) -> tuple[float, str | None]:
    """Return confidence clamped into 0.0-1.0 plus an audit note when changed."""
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0, f"invalid confidence {value!r}; clamped to 0.0"
    clamped = min(1.0, max(0.0, confidence))
    if clamped != confidence:
        return clamped, f"confidence {confidence!r} outside 0.0-1.0; clamped"
    return clamped, None


def _build_validated(
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
    """Construct and validate a complete NormalisedIssue dict."""
    issue: dict[str, Any] = {
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
        "title": finding.get("title", ""),
        "description": finding.get("description", ""),
        "fixClass": fix_class,
    }
    try:
        return NormalisedIssueModel.model_validate(issue).model_dump()
    except ValidationError as exc:
        logger.warning("issue_normaliser: schema repair forced manual_review: %s", exc)
        fallback = {
            **issue,
            "classification": "manual_review",
            "status": "manual_review",
            "severity": "low",
            "confidence": 0.0,
            "affectedPaths": [],
            "allowedFixClass": "",
            "evidence": [*evidence, f"schema validation failed: {exc}"],
        }
        return NormalisedIssueModel.model_validate(fallback).model_dump()


def _is_editorial(
    finding: dict[str, Any],
    model_router: "ModelRouter | None" = None,
) -> bool:
    """
    Return True when *finding* is an editorial quality issue.

    Clear editorial findings are handled deterministically. Clear structural or
    metadata findings return False. Ambiguous blog/transcript cases use the
    triage model when one is supplied and fail closed to editorial guidance when
    triage is unavailable.
    """
    title = str(finding.get("title", ""))
    description = str(finding.get("description", ""))
    category = str(finding.get("category", ""))
    combined = f"{title} {description} {category}"
    has_editorial = bool(_EDITORIAL_RE.search(combined))
    has_structural = bool(_STRUCTURAL_RE.search(combined))

    if has_editorial and not has_structural:
        return True
    if has_structural and not has_editorial:
        return False
    if has_editorial and has_structural:
        if model_router is None:
            return True
        return _triage_editorial(finding, model_router)
    return False


def _is_blog_or_transcript_path(paths: list[str]) -> bool:
    """Return True if any affected path is under blog/ or transcripts/."""
    return any(
        path.startswith(prefix) for path in paths for prefix in _ONBRAND_CONTENT_PREFIXES
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

    Uses OPENROUTER_TRIAGE_MODEL only. Falls back to True (treat as editorial,
    safer) on any error.
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
            "issue_normaliser: triage call failed (%s) - treating as editorial", exc
        )
        return True
