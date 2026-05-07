"""
Issue normaliser for the Repo Management Suite.

Converts raw audit findings into NormalisedIssue dicts, applying:
  - Classification logic (code_fix / future_guidance / manual_review / skipped)
  - Editorial guard for on-brand blog/transcript findings
  - Protected-path gate for mobile-ux (blog/posts/, transcripts/, etc.)
  - Task-ID generation
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId, Settings
    from repo_mgmt.model_router import ModelRouter

logger = logging.getLogger(__name__)

# Protected content path prefixes that the mobile-ux normaliser must block
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

# Editorial signal keywords for the heuristic fast-path
_EDITORIAL_KEYWORDS = re.compile(
    r"\b(tone|voice|punchiness|brand voice|wording|rewrite|rephrase|"
    r"quality|style|copy|messaging|engaging|compelling|punchy|vivid)\b",
    re.IGNORECASE,
)

# Approved fix classes per pipeline
_APPROVED_FIX_CLASSES: dict[str, frozenset[str]] = {
    "seo-aeo-geo": frozenset(
        [
            "route_fix",
            "config_fix",
            "schema_fix",
            "prompt_template_update",
            "audit_output_fix",
            "middleware_fix",
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
            "html_fix",
            "css_fix",
            "template_fix",
            "partial_fix",
            "redirect_fix",
            "prompt_template_update",
            "schema_fix",
            "meta_fix",
        ]
    ),
}

# Allowed fix classes for on-brand blog/transcript code_fix patches
_ON_BRAND_BLOG_ALLOWED: frozenset[str] = frozenset(
    ["html_fix", "template_fix", "schema_fix", "meta_fix", "partial_fix"]
)


def _is_editorial(finding: dict[str, Any], router: "ModelRouter | None") -> bool:
    """
    Return True if the finding describes a quality/tone/wording improvement
    rather than a concrete structural defect.

    Uses a fast heuristic first; falls back to OPENROUTER_TRIAGE_MODEL for
    ambiguous cases when a ModelRouter is provided.

    Args:
        finding: Raw finding dict from the audit snapshot.
        router: Optional ModelRouter to call for ambiguous classification.

    Returns:
        True if the finding is editorial (should be future_guidance).
    """
    description = str(finding.get("description", "")) + " " + str(finding.get("title", ""))

    # Fast-path heuristic: explicit structural/metadata signals → not editorial
    structural_signals = re.compile(
        r"\b(missing|malformed|broken|incorrect|invalid|schema|metadata|"
        r"tag|attribute|field|required|html|structure|defect)\b",
        re.IGNORECASE,
    )
    if structural_signals.search(description):
        return False

    # Fast-path heuristic: editorial signals → editorial
    if _EDITORIAL_KEYWORDS.search(description):
        return True

    # Ambiguous — ask triage model if available
    if router is not None:
        try:
            prompt = (
                "Classify this finding as either EDITORIAL or STRUCTURAL.\n"
                "EDITORIAL means the finding is about tone, voice, punchiness, "
                "wording quality, or style.\n"
                "STRUCTURAL means the finding is about a concrete code/metadata/HTML defect.\n"
                "Respond with exactly one word: EDITORIAL or STRUCTURAL.\n\n"
                f"Finding: {description[:800]}"
            )
            response = router.triage(prompt)
            return "EDITORIAL" in response.upper()
        except Exception as exc:
            logger.warning("_is_editorial triage call failed: %s — defaulting to editorial", exc)
            return True

    # No router and ambiguous — conservative: treat as editorial
    return True


def _touches_protected_mobile_ux(paths: list[str]) -> bool:
    """Return True if any path in *paths* is in a mobile-ux protected prefix."""
    for p in paths:
        for prefix in _MOBILE_UX_PROTECTED:
            if p == prefix or p.startswith(prefix):
                return True
    return False


def _make_task_id(pipeline_id: str, run_date: str, seq: int) -> str:
    """Generate a task ID in the format rms-<pipeline>-<YYYY-MM-DD>-<seq:03d>."""
    return f"rms-{pipeline_id}-{run_date}-{seq:03d}"


def normalise(
    audit: dict[str, Any],
    pipeline_id: "PipelineId",
    run_date: str,
    cfg: "Settings",
    router: "ModelRouter | None" = None,
) -> list[dict[str, Any]]:
    """
    Convert a raw audit snapshot dict into a list of NormalisedIssue dicts.

    Args:
        audit: Parsed audit JSON (from audit_reader.read_latest).
        pipeline_id: Pipeline being processed.
        run_date: ISO date string (YYYY-MM-DD) for task ID generation.
        cfg: Validated RMS settings.
        router: Optional ModelRouter for triage-model calls.

    Returns:
        List of NormalisedIssue dicts, each with a status of "pending" unless
        immediately classified otherwise.
    """
    raw_findings: list[dict[str, Any]] = audit.get("findings", [])
    approved_classes = _APPROVED_FIX_CLASSES[pipeline_id]
    validation_commands = cfg.validation_commands_for(pipeline_id)

    issues: list[dict[str, Any]] = []
    seq = 1

    for raw in raw_findings:
        task_id = _make_task_id(pipeline_id, run_date, seq)
        seq += 1

        affected_paths: list[str] = raw.get("affectedPaths", raw.get("affected_paths", []))
        suggested_fix_class: str = raw.get("fixClass", raw.get("fix_class", ""))
        severity: str = raw.get("severity", "medium")
        confidence: float = float(raw.get("confidence", 0.5))
        evidence: list[str] = raw.get("evidence", [])
        required_outcome: str = raw.get("requiredOutcome", raw.get("required_outcome", ""))
        source_audit: str = raw.get("sourceAudit", raw.get("source_audit", pipeline_id))

        # ── mobile-ux: block protected paths at normaliser level ────────────
        if pipeline_id == "mobile-ux" and _touches_protected_mobile_ux(affected_paths):
            logger.info(
                "issue_normaliser [mobile-ux]: skipping %s — touches protected paths %s",
                task_id,
                affected_paths,
            )
            issues.append(
                _build_issue(
                    task_id=task_id,
                    pipeline_id=pipeline_id,
                    source_audit=source_audit,
                    classification="skipped",
                    severity=severity,
                    confidence=confidence,
                    affected_paths=affected_paths,
                    evidence=evidence,
                    required_outcome=required_outcome,
                    allowed_fix_class=suggested_fix_class,
                    validation_commands=validation_commands,
                    status="skipped_not_actionable",
                )
            )
            continue

        # ── on-brand: editorial guard for blog/transcript findings ──────────
        if pipeline_id == "on-brand":
            touches_content = any(
                p.startswith("blog/posts/") or p.startswith("transcripts/")
                for p in affected_paths
            )
            if touches_content:
                if _is_editorial(raw, router):
                    logger.info(
                        "issue_normaliser [on-brand]: %s classified as future_guidance (editorial)",
                        task_id,
                    )
                    issues.append(
                        _build_issue(
                            task_id=task_id,
                            pipeline_id=pipeline_id,
                            source_audit=source_audit,
                            classification="future_guidance",
                            severity=severity,
                            confidence=confidence,
                            affected_paths=affected_paths,
                            evidence=evidence,
                            required_outcome=required_outcome,
                            allowed_fix_class=suggested_fix_class,
                            validation_commands=validation_commands,
                            status="future_guidance",
                        )
                    )
                    continue
                # Structural — verify fix class is allowed for blog/transcript
                if suggested_fix_class not in _ON_BRAND_BLOG_ALLOWED:
                    logger.info(
                        "issue_normaliser [on-brand]: %s fix_class %r not allowed for blog/transcript → manual_review",
                        task_id,
                        suggested_fix_class,
                    )
                    issues.append(
                        _build_issue(
                            task_id=task_id,
                            pipeline_id=pipeline_id,
                            source_audit=source_audit,
                            classification="manual_review",
                            severity=severity,
                            confidence=confidence,
                            affected_paths=affected_paths,
                            evidence=evidence,
                            required_outcome=required_outcome,
                            allowed_fix_class=suggested_fix_class,
                            validation_commands=validation_commands,
                            status="manual_review",
                        )
                    )
                    continue

        # ── Classify by fix class ────────────────────────────────────────────
        if suggested_fix_class in approved_classes:
            classification = "code_fix"
            status = "pending"
        elif suggested_fix_class == "future_guidance":
            classification = "future_guidance"
            status = "future_guidance"
        elif suggested_fix_class == "manual_review":
            classification = "manual_review"
            status = "manual_review"
        elif not suggested_fix_class:
            classification = "manual_review"
            status = "manual_review"
        else:
            # Unknown fix class for this pipeline
            logger.warning(
                "issue_normaliser [%s]: fix_class %r not in approved set → manual_review",
                pipeline_id,
                suggested_fix_class,
            )
            classification = "manual_review"
            status = "manual_review"

        issues.append(
            _build_issue(
                task_id=task_id,
                pipeline_id=pipeline_id,
                source_audit=source_audit,
                classification=classification,
                severity=severity,
                confidence=confidence,
                affected_paths=affected_paths,
                evidence=evidence,
                required_outcome=required_outcome,
                allowed_fix_class=suggested_fix_class,
                validation_commands=validation_commands,
                status=status,
            )
        )

    logger.info(
        "issue_normaliser [%s]: %d raw findings → %d issues",
        pipeline_id,
        len(raw_findings),
        len(issues),
    )
    return issues


def _build_issue(
    *,
    task_id: str,
    pipeline_id: str,
    source_audit: str,
    classification: str,
    severity: str,
    confidence: float,
    affected_paths: list[str],
    evidence: list[str],
    required_outcome: str,
    allowed_fix_class: str,
    validation_commands: list[str],
    status: str,
) -> dict[str, Any]:
    """Construct a NormalisedIssue dict from keyword arguments."""
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
    }
