"""
Audit reader for the Repo Management Suite.

Reads the latest audit JSON snapshot for a given pipeline from Cloudflare R2.
Returns an empty dict (with a warning log) if the key is absent — never raises.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId
    from repo_mgmt.r2_client import R2Client

logger = logging.getLogger(__name__)

# Mapping from pipeline ID to R2 audit key
_AUDIT_KEY_MAP: dict[str, str] = {
    "seo-aeo-geo": "audits/seo-aeo-geo/latest.json",
    "mobile-ux": "audits/mobile-ux/latest.json",
    "on-brand": "audits/on-brand/latest.json",
}


def read_latest(pipeline_id: "PipelineId", r2: "R2Client", bucket: str) -> dict:  # type: ignore[type-arg]
    """
    Read the latest audit JSON for *pipeline_id* from R2.

    Args:
        pipeline_id: One of "seo-aeo-geo", "mobile-ux", "on-brand".
        r2: Initialised R2Client instance.
        bucket: R2 bucket name to read from (typically cfg.r2_bucket_audits).

    Returns:
        Parsed dict from the audit JSON, or {} if the key is absent or unreadable.
    """
    key = _AUDIT_KEY_MAP[pipeline_id]
    try:
        raw = r2.get_object(bucket=bucket, key=key)
    except Exception as exc:
        logger.warning(
            "audit_reader: could not fetch %r from bucket %r: %s — returning empty audit",
            key,
            bucket,
            exc,
        )
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "audit_reader: %r is not valid JSON: %s — returning empty audit",
            key,
            exc,
        )
        return {}

    if not isinstance(data, dict):
        logger.warning(
            "audit_reader: %r parsed as %s (expected dict) — returning empty audit",
            key,
            type(data).__name__,
        )
        return {}

    logger.info("audit_reader: loaded %d-key snapshot from %r", len(data), key)
    return data
