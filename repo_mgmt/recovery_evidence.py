"""Safe, append-only recovery evidence writer for the existing R2 bucket.

This is not a lock service. Failed uploads are surfaced, not treated as
successful recovery. Call only from trusted workflows with scoped credentials.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_KEY = re.compile(r"^[a-f0-9]{64}$")
_TYPES = frozenset({"incidents", "attempts", "audit", "receipts"})


def evidence_key(repo: str, kind: str, incident_key: str, head_sha: str,\n                 receipt_id: str | None = None) -> str:
    """Build a path that cannot escape the repository's evidence prefix."""
    if not _REPO.fullmatch(repo) or repo in {".", ".."}:
        raise ValueError("invalid repository")
    if kind not in _TYPES:
        raise ValueError("invalid evidence category")
    if not _KEY.fullmatch(incident_key) or not _SHA.fullmatch(head_sha):
        raise ValueError("invalid incident key or SHA")
    if receipt_id is not None and not re.fullmatch(r"[a-f0-9]{32}", receipt_id):\n        raise ValueError("invalid receipt ID")\n    suffix = f"-{receipt_id}" if receipt_id else ""\n    return f"autonomous-recovery/{repo}/{kind}/{incident_key}/{head_sha.lower()}{suffix}.json"


def write_evidence(
    r2_client: Any, *, repo: str, kind: str, incident_key: str,
    head_sha: str, record: dict[str, Any],
    bucket: str = "hive-repositories",
) -> str:
    """Persist a bounded JSON receipt, using the existing RAMS R2Client."""
    key = evidence_key(repo, kind, incident_key, head_sha, uuid.uuid4().hex)
    if not isinstance(record, dict):
        raise ValueError("record must be an object")
    payload = json.dumps({
        "schema": "rams-autonomous-recovery/v1",
        "repository": repo,
        "incident_key": incident_key,
        "head_sha": head_sha.lower(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "record": record,
    }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > 65536:
        raise ValueError("evidence record exceeds 64 KiB")
    r2_client.put_object(bucket, key, payload, content_type="application/json")
    return key
