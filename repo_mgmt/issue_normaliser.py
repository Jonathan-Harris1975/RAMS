"""
Issue normaliser for the Repo Management Suite.

Converts raw audit findings into strict NormalisedIssue dicts. In production,
RMS audit ``latest.json`` files are manifests rather than finding ledgers, so
this module also understands enriched audit payloads produced by
``audit_reader.read_latest`` where child artefacts are attached under
``artefacts``.

The normaliser deliberately prefers safe manual-review or future-guidance tasks
when an audit finding lacks deterministic repo file evidence. That lets RAMS
surface real work without inventing patches. Tiny goblin, sturdy leash.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

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
    r"quality|compelling|snappier|more human|sounds|phrase|phrasing|spoken|"
    r"transition|anti-hype|filler|british spelling|american spelling)\b",
    re.IGNORECASE,
)
_STRUCTURAL_RE = re.compile(
    r"\b(schema|metadata|meta|template|partial|html|tag|attribute|markup|"
    r"broken|malformed|missing required|incorrect metadata|canonical|"
    r"structured data|validator|prompt|guardrail|middleware|route|config)\b",
    re.IGNORECASE,
)
_ONBRAND_STRUCTURAL_CLASSES: frozenset[str] = frozenset(
    ["html_fix", "template_fix", "schema_fix", "meta_fix", "partial_fix"]
)
_ONBRAND_CONTENT_PREFIXES = ("blog/", "transcripts/")
_FINDING_LIST_KEYS = (
    "findings",
    "issues",
    "rows",
    "defects",
    "confirmedDefectsLedger",
    "recommendations",
    "actions",
    "items",
)
_MAX_EXTRACTED_FINDINGS = 100


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
    raw_findings = _extract_findings(audit, pipeline_id, cfg)
    if not raw_findings:
        return []

    approved = _APPROVED_FIX_CLASSES.get(pipeline_id, frozenset())
    validation_commands = cfg.validation_commands_for(pipeline_id)
    results: list[dict[str, Any]] = []

    for seq, raw_finding in enumerate(raw_findings, start=1):
        finding = raw_finding if isinstance(raw_finding, dict) else {}
        metadata_errors: list[str] = []
        if not isinstance(raw_finding, dict):
            metadata_errors.append("finding is not a JSON object")

        affected_paths, path_errors = _safe_affected_paths(
            finding.get("affectedPaths", [])
        )
        metadata_errors.extend(path_errors)
        fix_class = _fix_class(finding)
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
                        allowed_fix_class="",
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
                        allowed_fix_class="",
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


# ── Live audit artefact extraction ────────────────────────────────────────


def _extract_findings(
    audit: dict[str, Any],
    pipeline_id: str,
    cfg: "Settings",
) -> list[dict[str, Any]]:
    """Return normaliser-ready finding dictionaries from latest or artefacts."""
    direct = audit.get("findings")
    if isinstance(direct, list) and direct:
        return [item for item in direct if isinstance(item, dict)]

    artefacts = audit.get("artefacts")
    if not isinstance(artefacts, dict):
        return []

    max_items = _normaliser_item_limit(cfg)
    mapped: list[dict[str, Any]] = []
    seen: set[str] = set()

    for artefact_name, payload in _ordered_artefacts(pipeline_id, artefacts):
        for candidate in _candidate_dicts(payload):
            finding = _map_candidate(candidate, pipeline_id, artefact_name)
            if not finding:
                continue
            signature = _finding_signature(finding)
            if signature in seen:
                continue
            seen.add(signature)
            mapped.append(finding)
            if len(mapped) >= max_items:
                logger.info(
                    "issue_normaliser: capped extracted %s findings at %d",
                    pipeline_id,
                    max_items,
                )
                return mapped
    return mapped


def _normaliser_item_limit(cfg: "Settings") -> int:
    """Return a bounded extraction cap to prevent enormous dry-run reports."""
    try:
        requested = int(getattr(cfg, "rms_max_issues_per_run", 5))
    except (TypeError, ValueError):
        requested = 5
    return max(1, min(_MAX_EXTRACTED_FINDINGS, requested * 10))


def _ordered_artefacts(
    pipeline_id: str,
    artefacts: dict[str, Any],
) -> list[tuple[str, Any]]:
    """Return artefacts in priority order for each pipeline."""
    priority = {
        "mobile-ux": [
            "repository-issue-appendix.json",
            "responsive-fix-appendix.json",
            "mandatory-mobile-scorecard.json",
            "focused-page-appendix.json",
            "report.json",
            "summary.json",
        ],
        "seo-aeo-geo": ["summary.json", "report.json", "coverage.json", "evidence.json"],
        "on-brand": ["report.json", "evidence.json", "summary.json"],
    }.get(pipeline_id, [])
    ordered: list[tuple[str, Any]] = []
    for name in priority:
        if name in artefacts:
            ordered.append((name, artefacts[name]))
    for name, payload in artefacts.items():
        if name not in {existing for existing, _ in ordered}:
            ordered.append((str(name), payload))
    return ordered


def _candidate_dicts(payload: Any) -> list[dict[str, Any]]:
    """Extract plausible issue dictionaries from nested JSON artefacts."""
    found: list[dict[str, Any]] = []

    def visit(value: Any, *, parent_key: str = "") -> None:
        if isinstance(value, dict):
            if _looks_like_issue(value):
                found.append(value)
            for key, child in value.items():
                if key in _FINDING_LIST_KEYS and isinstance(child, list):
                    for item in child:
                        if isinstance(item, dict) and _looks_like_issue(item):
                            found.append(item)
                        elif isinstance(item, (dict, list)):
                            visit(item, parent_key=key)
                elif isinstance(child, (dict, list)):
                    visit(child, parent_key=str(key))
        elif isinstance(value, list):
            for item in value:
                visit(item, parent_key=parent_key)

    visit(payload)
    return found


def _looks_like_issue(value: dict[str, Any]) -> bool:
    """Return True when a JSON object resembles an audit issue/finding."""
    keys = set(value)
    if keys & {"issueId", "issueID", "findingId", "findingID", "defectDescription"}:
        return True
    if keys & {"exactRemediation", "requiredOutcome", "acceptanceCriteria", "recommendation", "remediation"} and keys & {
        "severity",
        "check",
        "issueType",
        "title",
        "route",
        "url",
        "path",
    }:
        return True
    if keys & {"title", "summary", "description"} and keys & {"severity", "priority"} and keys & {"path", "route", "url", "recommendation", "remediation"}:
        return True
    if keys & {"violatedRule", "whyItIsOffBrand", "exactEvidence"}:
        return True
    return False


def _map_candidate(
    candidate: dict[str, Any], pipeline_id: str, artefact_name: str
) -> dict[str, Any] | None:
    """Map one live audit candidate to a raw finding dictionary."""
    if pipeline_id == "mobile-ux":
        return _map_mobile_candidate(candidate, artefact_name)
    if pipeline_id == "seo-aeo-geo":
        return _map_seo_candidate(candidate, artefact_name)
    if pipeline_id == "on-brand":
        return _map_on_brand_candidate(candidate, artefact_name)
    return None


def _map_mobile_candidate(candidate: dict[str, Any], artefact_name: str) -> dict[str, Any] | None:
    """Map a Mobile UX issue/row to a RAMS finding."""
    issue_id = _first_text(candidate, "issueId", "findingId") or "mobile-ux"
    route = _first_text(candidate, "route")
    url_or_path = _first_text(candidate, "exactUrlOrFilePath", "filePathOrUrl", "url", "path")
    affected = _paths_from_route_or_url(route, url_or_path)
    check = _first_text(candidate, "check", "issueType", "category") or "mobile UX"
    viewport = _first_text(candidate, "viewport")
    title_bits = [issue_id, check]
    if route or url_or_path:
        title_bits.append(route or url_or_path)
    if viewport:
        title_bits.append(f"{viewport}px")
    remediation = _first_text(candidate, "exactRemediation", "remediation", "recommendation")
    defect = _first_text(candidate, "defectDescription", "description", "consequence")
    if not remediation and not defect:
        return None
    evidence = _evidence_from_fields(
        candidate,
        [
            "defectDescription",
            "evidenceLabel",
            "consequence",
            "selectorComponentCodeAnchor",
            "bestAvailableAnchor",
            "currentEvidenceSnippet",
            "acceptanceCriteria",
            "verificationMethod",
        ],
    )
    screenshot_refs = candidate.get("screenshotRefs")
    if isinstance(screenshot_refs, list) and screenshot_refs:
        evidence.append(f"screenshotRefs: {len(screenshot_refs)} attached in source artefact")
    return {
        "title": " — ".join(part for part in title_bits if part),
        "description": defect or remediation,
        "severity": _map_severity(candidate.get("severity"), pipeline="mobile-ux"),
        "confidence": _confidence_from_candidate(candidate),
        "fixClass": _mobile_fix_class(check, remediation, candidate),
        "affectedPaths": affected,
        "evidence": evidence,
        "requiredOutcome": _join_sentences(
            remediation,
            _first_text(candidate, "acceptanceCriteria"),
            _first_text(candidate, "verificationMethod"),
        ),
        "sourceAudit": f"mobile-ux:{artefact_name}",
        "sourceIssueId": issue_id,
        "sourceArtefact": artefact_name,
        "route": route,
        "viewport": viewport,
        "check": check,
    }


def _map_seo_candidate(candidate: dict[str, Any], artefact_name: str) -> dict[str, Any] | None:
    """Map SEO/AEO/GEO summary or report objects to RAMS findings."""
    title = _first_text(
        candidate,
        "title",
        "issueTitle",
        "issueType",
        "check",
        "heading",
        "name",
        "label",
    )
    remediation = _first_text(
        candidate,
        "exactRemediation",
        "requiredOutcome",
        "recommendation",
        "remediation",
        "fix",
        "action",
        "nextStep",
        "acceptanceCriteria",
    )
    description = _first_text(
        candidate,
        "description",
        "defectDescription",
        "problem",
        "finding",
        "summary",
        "details",
        "consequence",
    )
    if not (title or remediation or description):
        return None
    affected = _paths_from_candidate(candidate)
    text = " ".join(str(part) for part in [title, remediation, description] if part)
    return {
        "title": title or f"SEO/AEO/GEO finding from {artefact_name}",
        "description": description or remediation or title,
        "severity": _map_severity(candidate.get("severity"), pipeline="seo-aeo-geo"),
        "confidence": _confidence_from_candidate(candidate),
        "fixClass": _derive_seo_fix_class(text, affected),
        "affectedPaths": affected,
        "evidence": _evidence_from_fields(
            candidate,
            [
                "evidence",
                "exactEvidence",
                "observed",
                "currentValue",
                "url",
                "route",
                "path",
                "verificationMethod",
            ],
        ),
        "requiredOutcome": remediation or description or title or "Review the SEO/AEO/GEO finding and determine the smallest safe source-level fix.",
        "sourceAudit": f"seo-aeo-geo:{artefact_name}",
        "sourceIssueId": _first_text(candidate, "issueId", "findingId", "id"),
        "sourceArtefact": artefact_name,
    }


def _map_on_brand_candidate(candidate: dict[str, Any], artefact_name: str) -> dict[str, Any] | None:
    """Map on-brand report defects to future-guidance or structural tasks."""
    issue_id = _first_text(candidate, "issueId", "findingId", "id") or "on-brand"
    issue_type = _first_text(candidate, "issueType", "violatedRule", "sourceType", "title")
    remediation = _first_text(
        candidate,
        "exactRemediation",
        "requiredOutcome",
        "recommendation",
        "improvedExample",
        "verificationMethod",
    )
    evidence_text = _first_text(candidate, "exactEvidence", "evidence", "whyItIsOffBrand")
    if not (issue_type or remediation or evidence_text):
        return None
    affected = _paths_from_candidate(candidate)
    source_type = _first_text(candidate, "sourceType")
    root_cause = _first_text(candidate, "rootCauseLevel")
    structural = bool(affected) and bool(_STRUCTURAL_RE.search(f"{issue_type} {remediation} {root_cause}"))
    return {
        "title": f"{issue_id}: {issue_type or 'on-brand finding'}",
        "description": _first_text(candidate, "whyItIsOffBrand", "description") or evidence_text or remediation,
        "severity": _map_severity(candidate.get("severity"), pipeline="on-brand"),
        "confidence": _confidence_from_candidate(candidate),
        "fixClass": _derive_on_brand_fix_class(candidate) if structural else "future_guidance",
        "classification": "code_fix" if structural else "future_guidance",
        "affectedPaths": affected,
        "evidence": _evidence_from_fields(
            candidate,
            [
                "exactEvidence",
                "whyItIsOffBrand",
                "violatedRule",
                "rootCauseLevel",
                "itemTitleOrId",
                "sourceType",
                "verificationMethod",
            ],
        ),
        "requiredOutcome": remediation or "Use this historic on-brand evidence to tighten future generated output and QA guardrails.",
        "sourceAudit": f"on-brand:{artefact_name}",
        "sourceIssueId": issue_id,
        "sourceArtefact": artefact_name,
        "sourceType": source_type,
    }


def _finding_signature(finding: dict[str, Any]) -> str:
    """Return a stable de-duplication signature for mapped findings."""
    return "|".join(
        [
            str(finding.get("sourceAudit", "")),
            str(finding.get("sourceIssueId", "")),
            str(finding.get("title", "")),
            ",".join(str(path) for path in finding.get("affectedPaths", [])),
        ]
    )


# ── Field mapping helpers ─────────────────────────────────────────────────


def _fix_class(finding: dict[str, Any]) -> str:
    """Return supported fix-class aliases from a raw finding."""
    return str(
        finding.get("fixClass")
        or finding.get("allowedFixClass")
        or finding.get("fix_class")
        or ""
    ).strip()


def _first_text(candidate: dict[str, Any], *keys: str) -> str:
    """Return the first non-empty textual value for the supplied keys."""
    for key in keys:
        value = candidate.get(key)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _join_sentences(*parts: str) -> str:
    """Join non-empty sentence fragments without duplicating whitespace."""
    return " ".join(part.strip() for part in parts if part and part.strip())


def _evidence_from_fields(candidate: dict[str, Any], keys: list[str]) -> list[str]:
    """Build concise evidence strings from heterogeneous audit fields."""
    evidence: list[str] = []
    for key in keys:
        value = candidate.get(key)
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            if all(not isinstance(item, (dict, list)) for item in value):
                joined = "; ".join(str(item) for item in value[:5])
                if joined:
                    evidence.append(f"{key}: {joined}")
            else:
                evidence.append(f"{key}: {len(value)} item(s)")
        elif isinstance(value, dict):
            evidence.append(f"{key}: {len(value)} field(s)")
        else:
            text = str(value).strip()
            if text:
                evidence.append(f"{key}: {text}")
    return evidence


def _paths_from_candidate(candidate: dict[str, Any]) -> list[str]:
    """Extract repo-relative paths from common finding fields."""
    existing = candidate.get("affectedPaths")
    if isinstance(existing, list):
        paths, _ = _safe_affected_paths(existing)
        if paths:
            return paths
    for key in ("file", "filePath", "path", "repoPath", "exactFilePath"):
        text = _first_text(candidate, key)
        if text:
            paths = _paths_from_route_or_url("", text)
            if paths:
                return paths
    route = _first_text(candidate, "route")
    url = _first_text(candidate, "url", "exactUrlOrFilePath", "filePathOrUrl")
    return _paths_from_route_or_url(route, url)


def _paths_from_route_or_url(route: str, url_or_path: str) -> list[str]:
    """Map a route, URL, or repo path to a safe repo-relative HTML path."""
    path = ""
    if route:
        path = route
    elif url_or_path:
        parsed = urlparse(url_or_path)
        if parsed.scheme and parsed.netloc:
            path = parsed.path or "/"
        else:
            path = url_or_path
    path = path.strip()
    if not path:
        return []
    if path.startswith("https://") or path.startswith("http://"):
        parsed = urlparse(path)
        path = parsed.path or "/"
    if path.startswith("/"):
        path = path.lstrip("/")
        if not path:
            path = "index.html"
        elif path.endswith("/"):
            path = f"{path}index.html"
        elif "." not in path.rsplit("/", 1)[-1]:
            path = f"{path}/index.html"
    try:
        return [normalise_repo_relative_path(path)]
    except ValueError:
        return []


def _map_severity(value: Any, *, pipeline: str) -> str:
    """Map native audit severities into RAMS' strict severity enum."""
    text = str(value or "").strip().lower()
    mapping = {
        "p0": "critical",
        "0": "critical",
        "critical": "critical",
        "blocker": "critical",
        "p1": "high",
        "1": "high",
        "high": "high",
        "major": "high",
        "p2": "medium",
        "2": "medium",
        "medium": "medium",
        "moderate": "medium",
        "p3": "low",
        "3": "low",
        "low": "low",
        "minor": "low",
        "info": "low",
    }
    return mapping.get(text, "medium" if pipeline == "mobile-ux" else "low")


def _confidence_from_candidate(candidate: dict[str, Any]) -> float:
    """Return a numeric confidence for confirmed or free-form audit fields."""
    value = candidate.get("confidence")
    if isinstance(value, (int, float)):
        return min(1.0, max(0.0, float(value)))
    text = str(value or "").strip().lower()
    if text in {"confirmed", "certain", "high"}:
        return 0.95
    if text in {"medium", "moderate"}:
        return 0.75
    if text in {"low", "weak"}:
        return 0.5
    severity = str(candidate.get("severity", "")).strip().lower()
    if severity in {"p0", "critical", "high", "p1"}:
        return 0.9
    return 0.8


def _mobile_fix_class(check: str, remediation: str, candidate: dict[str, Any]) -> str:
    """Derive a mobile-ux approved fix class from check/remediation text."""
    text = f"{check} {remediation} {_first_text(candidate, 'selectorComponentCodeAnchor', 'bestAvailableAnchor')}".lower()
    if "viewport" in text:
        return "viewport_fix"
    if any(word in text for word in ("tap", "navigation", "hamburger", "aria", "focus", "escape", "cta")):
        return "accessibility_fix"
    if any(word in text for word in ("overflow", "typography", "spacing", "clip", "wrap", "layout", "css")):
        return "css_fix"
    if any(word in text for word in ("meta", "head")):
        return "meta_fix"
    return "html_fix"


def _derive_seo_fix_class(text: str, affected_paths: list[str]) -> str:
    """Derive an SEO/AEO/GEO fix class from finding text and affected path."""
    lowered = f"{text} {' '.join(affected_paths)}".lower()
    if "schema" in lowered or "json-ld" in lowered or "structured data" in lowered:
        return "schema_fix"
    if "canonical" in lowered:
        return "canonical_fix"
    if "sitemap" in lowered:
        return "sitemap_fix"
    if "robots" in lowered:
        return "robots_fix"
    if "llms" in lowered:
        return "llms_fix"
    if "redirect" in lowered:
        return "redirect_fix"
    if "meta" in lowered or "description" in lowered or "title" in lowered:
        return "meta_fix"
    if any(path.endswith(".css") for path in affected_paths):
        return "css_fix"
    if affected_paths:
        return "html_fix"
    return "future_guidance"


def _derive_on_brand_fix_class(candidate: dict[str, Any]) -> str:
    """Derive an on-brand code fix class only for concrete structural items."""
    text = " ".join(str(candidate.get(key, "")) for key in ("issueType", "exactRemediation", "rootCauseLevel", "violatedRule")).lower()
    if "prompt" in text or "guardrail" in text:
        return "prompt_template_update"
    if "schema" in text:
        return "schema_fix"
    if "metadata" in text or "meta" in text:
        return "meta_fix"
    if "middleware" in text:
        return "middleware_fix"
    if "route" in text:
        return "route_fix"
    if "config" in text or "validator" in text:
        return "config_fix"
    return "audit_output_fix"


# ── Existing safety/validation helpers ────────────────────────────────────


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
    text = str(value).strip().lower()
    known = {
        "p0",
        "0",
        "critical",
        "blocker",
        "p1",
        "1",
        "high",
        "major",
        "p2",
        "2",
        "medium",
        "moderate",
        "p3",
        "3",
        "low",
        "minor",
        "info",
    }
    if text in known:
        return _map_severity(value, pipeline=""), None
    return "low", f"invalid severity {value!r}; routed to manual_review"


def _safe_confidence(value: Any) -> tuple[float, str | None]:
    """Return confidence clamped into 0.0-1.0 plus an audit note when changed."""
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        text = str(value or "").strip().lower()
        if text in {"confirmed", "certain", "high", "medium", "moderate", "low", "weak"}:
            return _confidence_from_candidate({"confidence": value}), None
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
    for key in ("sourceIssueId", "sourceArtefact", "route", "viewport", "check", "sourceType"):
        if key in finding and finding[key] not in (None, ""):
            issue[key] = finding[key]
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
