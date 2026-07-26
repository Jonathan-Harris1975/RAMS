"""
Audit reader for the Repo Management Suite.

Reads the latest audit JSON manifest for a given pipeline from Cloudflare R2,
then follows the manifest's JSON artefact pointers so downstream code receives
both the lightweight ``latest.json`` signpost and the real report appendices
that contain actionable findings.

Missing child artefacts are logged and recorded, but they never abort a run.
A missing or invalid latest snapshot still returns ``{}`` so pipeline execution
remains fail-soft for scheduled dry-runs.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId
    from repo_mgmt.r2_client import R2Client

logger = logging.getLogger(__name__)

# Mapping from pipeline ID to R2 audit key.
_AUDIT_KEY_MAP: dict[str, tuple[str, ...]] = {
    # Unified website audits are normally read by exact key supplied by AIMS.
    # This legacy pointer is only a fail-soft fallback for manual/CLI calls.
    "website": ("audits/website/latest.json",),
    # Council reports are preferred master reports when present.
    # Raw source audits remain fallback inputs so staged deployments stay backward compatible.
    "seo-aeo-geo": (
        "audits/seo-aeo-geo-council/latest.json",
        "audits/seo-aeo-geo/latest.json",
    ),
    "mobile-ux": (
        "audits/mobile-ux-council/latest.json",
        "audits/mobile-ux/latest.json",
    ),
    "on-brand": (
        "audits/brand-social-council/latest.json",
        "audits/on-brand/latest.json",
    ),
}

# Supplemental source-owner reports are merged into the on-brand RAMS evidence
# pack. They are AIMS/R2-owned and must not replace the master council latest;
# they simply give RAMS the separate podcast/transcript evidence without making
# the static website audit carry those dynamic routes.
_SUPPLEMENTAL_AUDIT_KEY_MAP: dict[str, tuple[str, ...]] = {
    "on-brand": (
        "audits/podcast-episode/latest.json",
        "audits/podcast-transcript/latest.json",
    ),
}

# Keep dereferencing bounded and JSON-only. Screenshot-heavy manifests can name
# hundreds of artefacts; RAMS only needs structured evidence documents.
_PIPELINE_ARTEFACT_PRIORITIES: dict[str, tuple[str, ...]] = {
    "website": ("website-audit.json",),
    "mobile-ux": (
        "repository-issue-appendix.json",
        "responsive-fix-appendix.json",
        "mandatory-mobile-scorecard.json",
        "focused-page-appendix.json",
        "summary.json",
        "coverage.json",
        "report.json",
        "evidence.json",
        "execution.json",
        "preflight.json",
        "reconciliation.json",
    ),
    "seo-aeo-geo": (
        "repository-issue-appendix.json",
        "summary.json",
        "coverage.json",
        "report.json",
        "evidence.json",
        "execution.json",
        "preflight.json",
    ),
    "on-brand": (
        "repository-issue-appendix.json",
        "report.json",
        "evidence.json",
        "summary.json",
        "coverage.json",
    ),
}
_MAX_ARTEFACTS_PER_RUN = 12
_DEFAULT_MAX_OBJECT_BYTES = 2 * 1024 * 1024
_DEFAULT_MAX_TOTAL_BYTES = 8 * 1024 * 1024


@dataclass
class _ReadBudget:
    remaining_bytes: int
    remaining_artefacts: int


def read_latest(
    pipeline_id: "PipelineId",
    r2: "R2Client",
    bucket: str,
    *,
    max_artefacts: int = _MAX_ARTEFACTS_PER_RUN,
    max_object_bytes: int = _DEFAULT_MAX_OBJECT_BYTES,
    max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
) -> dict[str, object]:
    """
    Read and enrich the latest audit JSON for *pipeline_id* from R2.

    Args:
        pipeline_id: One of ``website``, ``seo-aeo-geo``, ``mobile-ux``, or ``on-brand``.
        r2: Initialised R2Client instance.
        bucket: R2 bucket name to read from, usually ``cfg.r2_bucket_audits``.

    Returns:
        Parsed latest snapshot, enriched with optional ``artefacts``,
        ``artefactKeys`` and ``artefactErrors`` dictionaries. Returns ``{}`` if
        the latest snapshot itself is absent or invalid.
    """
    budget = _ReadBudget(
        remaining_bytes=max_total_bytes, remaining_artefacts=max_artefacts
    )
    key, data = _read_first_latest_snapshot(
        pipeline_id, r2, bucket, budget=budget, max_object_bytes=max_object_bytes
    )
    if not data:
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "audit_reader: %r parsed as %s (expected dict) — returning empty audit",
            key,
            type(data).__name__,
        )
        return {}

    artefact_keys = _discover_artefact_keys(pipeline_id, data)
    artefacts: dict[str, Any] = {}
    artefact_key_map: dict[str, str] = {}
    artefact_errors: dict[str, str] = {}

    for label, artefact_key in artefact_keys.items():
        if budget.remaining_artefacts <= 0 or budget.remaining_bytes <= 0:
            artefact_errors[label] = "RAMS audit evidence budget exhausted"
            break
        child = _read_json_object(
            r2,
            bucket,
            artefact_key,
            fail_soft=True,
            budget=budget,
            max_object_bytes=max_object_bytes,
            count_as_artefact=True,
        )
        if child is None:
            artefact_errors[label] = f"unreadable JSON artefact: {artefact_key}"
            continue
        artefacts[label] = child
        artefact_key_map[label] = artefact_key

    supplemental = _empty_supplemental_bundle()
    if _should_load_supplemental_reports(pipeline_id, data):
        supplemental = _load_supplemental_snapshots(
            pipeline_id, r2, bucket, budget=budget, max_object_bytes=max_object_bytes
        )
    if supplemental["artefacts"]:
        artefacts.update(supplemental["artefacts"])
        artefact_key_map.update(supplemental["artefactKeys"])
    if supplemental["artefactErrors"]:
        artefact_errors.update(supplemental["artefactErrors"])

    if artefacts:
        data = dict(data)
        data["latest"] = {
            key_: value
            for key_, value in data.items()
            if key_
            not in {
                "artefacts",
                "artefactKeys",
                "artefactErrors",
                "latest",
                "supplementalLatest",
            }
        }
        data["artefacts"] = artefacts
        data["artefactKeys"] = artefact_key_map
        if supplemental["latest"]:
            data["supplementalLatest"] = supplemental["latest"]
        if artefact_errors:
            data["artefactErrors"] = artefact_errors
        logger.info(
            "audit_reader: loaded %d-key snapshot from %r with %d JSON artefact(s)",
            len(data.get("latest", {})),
            key,
            len(artefacts),
        )
    else:
        if artefact_errors or supplemental["latest"]:
            data = dict(data)
            if supplemental["latest"]:
                data["supplementalLatest"] = supplemental["latest"]
            if artefact_errors:
                data["artefactErrors"] = artefact_errors
        logger.info("audit_reader: loaded %d-key snapshot from %r", len(data), key)
    return data


def validate_website_report_key(key: str) -> str:
    """Validate the exact final website-audit JSON key supplied by AIMS."""
    cleaned = _clean_key(str(key or ""))
    parts = PurePosixPath(cleaned).parts
    if (
        len(parts) != 5
        or parts[0] != "audits"
        or parts[1] != "website"
        or not re.fullmatch(r"\d{4}-\d{2}", parts[2])
        or not re.fullmatch(r"[A-Za-z0-9._-]+", parts[3])
        or parts[4] != "website-audit.json"
    ):
        raise ValueError(
            "website audit key must match audits/website/YYYY-MM/<session>/website-audit.json"
        )
    return cleaned


def read_report_key(
    pipeline_id: "PipelineId",
    r2: "R2Client",
    bucket: str,
    key: str,
    *,
    max_object_bytes: int = _DEFAULT_MAX_OBJECT_BYTES,
) -> dict[str, object]:
    """Read one exact machine-readable audit report instead of a latest pointer.

    The unified website pipeline intentionally retains only PDF, HTML and JSON,
    so AIMS passes the exact JSON key to RAMS at dispatch time. This keeps the
    audit bucket free of a permanent ``latest.json`` signpost.
    """
    if pipeline_id != "website":
        raise ValueError("exact report-key reads are currently supported only for website")
    final_key = validate_website_report_key(key)
    budget = _ReadBudget(remaining_bytes=max_object_bytes, remaining_artefacts=0)
    data = _read_json_object(
        r2,
        bucket,
        final_key,
        fail_soft=False,
        budget=budget,
        max_object_bytes=max_object_bytes,
    )
    if not isinstance(data, dict):
        return {}
    if data.get("auditType") != "website":
        logger.warning("audit_reader: exact website report %r has wrong auditType", final_key)
        return {}
    if data.get("schemaVersion") != "website-audit-report/v1":
        logger.warning(
            "audit_reader: exact website report %r has unsupported schemaVersion=%r",
            final_key,
            data.get("schemaVersion"),
        )
        return {}
    if data.get("remediationContractVersion") != "rams-website/v1":
        logger.warning(
            "audit_reader: exact website report %r has unsupported remediationContractVersion=%r",
            final_key,
            data.get("remediationContractVersion"),
        )
        return {}
    session_id = PurePosixPath(final_key).parts[3]
    if data.get("sessionId") != session_id:
        logger.warning(
            "audit_reader: exact website report %r has sessionId=%r (expected %r)",
            final_key,
            data.get("sessionId"),
            session_id,
        )
        return {}
    report_set = data.get("reportSet")
    expected_prefix = str(PurePosixPath(final_key).parent)
    expected_keys = {
        "pdf": f"{expected_prefix}/website-audit.pdf",
        "html": f"{expected_prefix}/website-audit.html",
        "json": final_key,
    }
    if not isinstance(report_set, dict) or any(
        not isinstance(report_set.get(label), dict)
        or report_set[label].get("key") != expected_key
        for label, expected_key in expected_keys.items()
    ):
        logger.warning(
            "audit_reader: exact website report %r does not declare the required PDF/HTML/JSON sibling report set",
            final_key,
        )
        return {}
    if data.get("retentionPolicy") != "final-pdf-html-json-only":
        logger.warning(
            "audit_reader: exact website report %r has unsupported retentionPolicy=%r",
            final_key,
            data.get("retentionPolicy"),
        )
        return {}
    result = dict(data)
    result["sourceAuditKey"] = final_key
    return result


def _empty_supplemental_bundle() -> dict[str, Any]:
    return {"latest": {}, "artefacts": {}, "artefactKeys": {}, "artefactErrors": {}}


def _should_load_supplemental_reports(pipeline_id: str, latest: dict[str, Any]) -> bool:
    if pipeline_id not in _SUPPLEMENTAL_AUDIT_KEY_MAP:
        return False
    return bool(
        latest.get("auditType")
        or latest.get("reportPrefix")
        or any(str(key).endswith("Url") for key in latest)
    )


def _load_supplemental_snapshots(
    pipeline_id: str,
    r2: "R2Client",
    bucket: str,
    *,
    budget: _ReadBudget,
    max_object_bytes: int,
) -> dict[str, Any]:
    """Load optional source-owner reports that support a master pipeline.

    Supplemental reports are fail-soft and namespaced so their report.json,
    summary.json and repository-issue-appendix.json artefacts do not collide
    with the primary audit/council artefacts.
    """
    latest_by_label: dict[str, Any] = {}
    artefacts: dict[str, Any] = {}
    artefact_keys: dict[str, str] = {}
    artefact_errors: dict[str, str] = {}

    for latest_key in _SUPPLEMENTAL_AUDIT_KEY_MAP.get(pipeline_id, ()):
        label = _supplemental_label(latest_key)
        latest = _read_json_object(
            r2,
            bucket,
            latest_key,
            fail_soft=True,
            budget=budget,
            max_object_bytes=max_object_bytes,
        )
        if not isinstance(latest, dict) or not latest:
            continue
        latest_by_label[label] = latest
        for child_label, artefact_key in _discover_artefact_keys(label, latest).items():
            namespaced_label = f"{label}:{child_label}"
            if budget.remaining_artefacts <= 0 or budget.remaining_bytes <= 0:
                artefact_errors[namespaced_label] = (
                    "RAMS audit evidence budget exhausted"
                )
                break
            child = _read_json_object(
                r2,
                bucket,
                artefact_key,
                fail_soft=True,
                budget=budget,
                max_object_bytes=max_object_bytes,
                count_as_artefact=True,
            )
            if child is None:
                artefact_errors[namespaced_label] = (
                    f"unreadable JSON artefact: {artefact_key}"
                )
                continue
            artefacts[namespaced_label] = child
            artefact_keys[namespaced_label] = artefact_key

    return {
        "latest": latest_by_label,
        "artefacts": artefacts,
        "artefactKeys": artefact_keys,
        "artefactErrors": artefact_errors,
    }


def _supplemental_label(latest_key: str) -> str:
    parts = PurePosixPath(latest_key).parts
    try:
        audit_index = parts.index("audits")
        return parts[audit_index + 1]
    except (ValueError, IndexError):
        return PurePosixPath(latest_key).parent.name or "supplemental"


def _read_first_latest_snapshot(
    pipeline_id: "PipelineId",
    r2: "R2Client",
    bucket: str,
    *,
    budget: _ReadBudget,
    max_object_bytes: int,
) -> tuple[str, Any | None]:
    """Read the first available latest snapshot for a pipeline.

    Pipelines prefer their council master reports, falling back to raw audit
    reports when a council has not run yet.
    """
    keys = _AUDIT_KEY_MAP[pipeline_id]
    last_key = keys[-1]
    for key in keys:
        data = _read_json_object(
            r2,
            bucket,
            key,
            fail_soft=(key != last_key),
            budget=budget,
            max_object_bytes=max_object_bytes,
        )
        if data:
            if key != last_key:
                logger.info(
                    "audit_reader: using preferred latest snapshot %r for %s",
                    key,
                    pipeline_id,
                )
            return key, data
    return last_key, None


def _read_json_object(
    r2: "R2Client",
    bucket: str,
    key: str,
    *,
    fail_soft: bool = False,
    budget: _ReadBudget | None = None,
    max_object_bytes: int = _DEFAULT_MAX_OBJECT_BYTES,
    count_as_artefact: bool = False,
) -> Any | None:
    """Read one bounded R2 JSON object, optionally returning None on failure."""
    if budget is not None and budget.remaining_bytes <= 0:
        return None
    allowed = max_object_bytes
    if budget is not None:
        allowed = min(allowed, budget.remaining_bytes)
    try:
        limited = getattr(type(r2), "get_object_limited", None)
        if callable(limited):
            raw = r2.get_object_limited(bucket=bucket, key=key, max_bytes=allowed)
        else:
            raw = r2.get_object(bucket=bucket, key=key)
            if len(raw) > allowed:
                raise ValueError(f"object exceeds {allowed} byte RAMS limit")
        if budget is not None:
            budget.remaining_bytes -= len(raw)
            if count_as_artefact:
                budget.remaining_artefacts -= 1
    except Exception as exc:
        if fail_soft:
            logger.warning(
                "audit_reader: could not fetch child artefact %r: %s", key, exc
            )
            return None
        logger.warning(
            "audit_reader: could not fetch %r from bucket %r: %s — returning empty audit",
            key,
            bucket,
            exc,
        )
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        if fail_soft:
            logger.warning(
                "audit_reader: child artefact %r is not valid JSON: %s", key, exc
            )
            return None
        logger.warning(
            "audit_reader: %r is not valid JSON: %s — returning empty audit",
            key,
            exc,
        )
        return None


def _discover_artefact_keys(pipeline_id: str, latest: dict[str, Any]) -> dict[str, str]:
    """Return ordered child JSON artefact labels mapped to R2 object keys."""
    candidates: dict[str, str] = {}
    report_prefix = _clean_key(str(latest.get("reportPrefix", "")))
    priorities = _PIPELINE_ARTEFACT_PRIORITIES.get(pipeline_id, ())

    # Explicit URL fields: reportJsonUrl, summaryUrl, evidenceUrl, etc.
    for field, value in latest.items():
        if field.endswith("Url") and isinstance(value, str):
            key = _key_from_url(value)
            if key and key.endswith(".json"):
                candidates[_label_from_field_or_key(field, key)] = key

    # Artefact manifest entries: {"summary.json": "https://.../summary.json"}
    manifest = latest.get("artefacts")
    if isinstance(manifest, dict):
        for label, value in manifest.items():
            label_text = str(label)
            if not label_text.endswith(".json"):
                continue
            key = _key_from_url(str(value)) if isinstance(value, str) else ""
            if not key and report_prefix:
                key = _join_key(report_prefix, label_text)
            if key and key.endswith(".json"):
                candidates[_safe_label(label_text)] = key

    # If the manifest exposes explicit JSON URLs/artefact entries, trust those.
    # Older audit writers sometimes omit child JSON pointers entirely; only then
    # derive a bounded set of likely keys from reportPrefix. This avoids noisy
    # NoSuchKey warnings for artefacts the producer never wrote.
    if report_prefix and not candidates:
        for label in priorities:
            candidates.setdefault(label, _join_key(report_prefix, label))

    # Final ordering: pipeline priority first, then any remaining discovered JSON.
    ordered: dict[str, str] = {}
    for label in priorities:
        if label in candidates:
            ordered[label] = candidates[label]
    for label, key in candidates.items():
        if label not in ordered and _is_supported_json_label(label):
            ordered[label] = key
    return ordered


def _label_from_field_or_key(field: str, key: str) -> str:
    """Convert a manifest URL field or key path into a stable filename label."""
    filename = PurePosixPath(key).name
    if filename.endswith(".json"):
        return filename
    stripped = field[:-3] if field.endswith("Url") else field
    return f"{stripped}.json"


def _is_supported_json_label(label: str) -> bool:
    """Return True for structured JSON labels and False for noisy screenshots."""
    lowered = label.lower()
    if not lowered.endswith(".json"):
        return False
    return not any(part in lowered for part in ("screenshot", "capability-probe"))


def _safe_label(label: str) -> str:
    """Return a compact artefact label with only the final path component."""
    return PurePosixPath(label.replace("\\", "/")).name


def _key_from_url(value: str) -> str:
    """Extract an R2 object key from a public R2 URL or return an empty string."""
    parsed = urlparse(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return _clean_key(value) if value.endswith(".json") else ""
    return _clean_key(unquote(parsed.path.lstrip("/")))


def _join_key(prefix: str, suffix: str) -> str:
    """Join two R2 key fragments using forward slashes."""
    return f"{_clean_key(prefix).rstrip('/')}/{_clean_key(suffix).lstrip('/')}"


def _clean_key(value: str) -> str:
    """Normalise an R2 key-like string without allowing traversal semantics."""
    cleaned = value.strip().replace("\\", "/").lstrip("/")
    parts = [part for part in cleaned.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return ""
    return "/".join(parts)
