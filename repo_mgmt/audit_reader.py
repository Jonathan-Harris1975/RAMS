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
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId
    from repo_mgmt.r2_client import R2Client

logger = logging.getLogger(__name__)

# Mapping from pipeline ID to R2 audit key.
_AUDIT_KEY_MAP: dict[str, str] = {
    "seo-aeo-geo": "audits/seo-aeo-geo/latest.json",
    "mobile-ux": "audits/mobile-ux/latest.json",
    "on-brand": "audits/on-brand/latest.json",
}

# Keep dereferencing bounded and JSON-only. Screenshot-heavy manifests can name
# hundreds of artefacts; RAMS only needs structured evidence documents.
_PIPELINE_ARTEFACT_PRIORITIES: dict[str, tuple[str, ...]] = {
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
        "summary.json",
        "coverage.json",
        "report.json",
        "evidence.json",
        "execution.json",
        "preflight.json",
    ),
    "on-brand": (
        "report.json",
        "evidence.json",
        "summary.json",
        "coverage.json",
    ),
}
_MAX_ARTEFACTS_PER_RUN = 12


def read_latest(pipeline_id: "PipelineId", r2: "R2Client", bucket: str) -> dict[str, object]:
    """
    Read and enrich the latest audit JSON for *pipeline_id* from R2.

    Args:
        pipeline_id: One of ``seo-aeo-geo``, ``mobile-ux``, or ``on-brand``.
        r2: Initialised R2Client instance.
        bucket: R2 bucket name to read from, usually ``cfg.r2_bucket_audits``.

    Returns:
        Parsed latest snapshot, enriched with optional ``artefacts``,
        ``artefactKeys`` and ``artefactErrors`` dictionaries. Returns ``{}`` if
        the latest snapshot itself is absent or invalid.
    """
    key = _AUDIT_KEY_MAP[pipeline_id]
    data = _read_json_object(r2, bucket, key)
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

    for label, artefact_key in list(artefact_keys.items())[:_MAX_ARTEFACTS_PER_RUN]:
        child = _read_json_object(r2, bucket, artefact_key, fail_soft=True)
        if child is None:
            artefact_errors[label] = f"unreadable JSON artefact: {artefact_key}"
            continue
        artefacts[label] = child
        artefact_key_map[label] = artefact_key

    if artefacts:
        data = dict(data)
        data["latest"] = {key_: value for key_, value in data.items() if key_ not in {"artefacts", "artefactKeys", "artefactErrors", "latest"}}
        data["artefacts"] = artefacts
        data["artefactKeys"] = artefact_key_map
        if artefact_errors:
            data["artefactErrors"] = artefact_errors
        logger.info(
            "audit_reader: loaded %d-key snapshot from %r with %d JSON artefact(s)",
            len(data.get("latest", {})),
            key,
            len(artefacts),
        )
    else:
        if artefact_errors:
            data = dict(data)
            data["artefactErrors"] = artefact_errors
        logger.info("audit_reader: loaded %d-key snapshot from %r", len(data), key)
    return data


def _read_json_object(
    r2: "R2Client",
    bucket: str,
    key: str,
    *,
    fail_soft: bool = False,
) -> Any | None:
    """Read one R2 key and parse JSON, optionally returning None on failure."""
    try:
        raw = r2.get_object(bucket=bucket, key=key)
    except Exception as exc:
        if fail_soft:
            logger.warning("audit_reader: could not fetch child artefact %r: %s", key, exc)
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
            logger.warning("audit_reader: child artefact %r is not valid JSON: %s", key, exc)
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
