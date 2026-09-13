"""Shared model-tier and premium-approval policy helpers for RAMS."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

_PREMIUM_MODEL_PATTERN = re.compile(
    r"(?:^|/)(?:"
    r"claude-[^/]*opus(?:[-.:]|$)"
    r"|gpt-4(?:o)?(?!-(?:mini|nano))(?:[-.:]|$)"
    r"|gpt-5(?:\.\d+)?-pro(?:[-.:]|$)"
    r"|o[134]-pro(?:[-.:]|$)"
    r")",
    flags=re.IGNORECASE,
)


def _true(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_premium_model(
    model_id: str,
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether a model requires an explicit premium-use justification.

    HIVE may mark new or unusually expensive models explicitly.  The local name
    check is deliberately narrow and protects known expert families even if an
    incoming registry omits its tier metadata.  Mini/nano GPT-4 variants are not
    caught by the pattern.
    """
    data = metadata or {}
    if _true(data.get("premium")) or _true(data.get("requires_justification")):
        return True
    tier = str(data.get("tier") or data.get("model_tier") or "").strip().lower()
    if tier in {"expert", "premium"}:
        return True
    return _PREMIUM_MODEL_PATTERN.search(str(model_id or "").strip()) is not None


def clean_justification_id(value: object) -> str:
    """Return a bounded, log-safe approval identifier."""
    return str(value or "").strip()[:160]


def premium_approval_is_active(
    value: object,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether an ISO-8601 or Unix approval expiry is still in the future."""
    if value in (None, ""):
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            expires_at = datetime.fromtimestamp(float(value), tz=UTC)
        except (OSError, OverflowError, ValueError):
            return False
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            expires_at = datetime.fromisoformat(text)
        except ValueError:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        expires_at = expires_at.astimezone(UTC)
    return expires_at > (now or datetime.now(UTC))
