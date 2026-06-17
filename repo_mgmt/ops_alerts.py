"""Bounded, redacted operational alert delivery to the HIVE event inbox."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from repo_mgmt.config import Settings

logger = logging.getLogger(__name__)


def send_operational_event(settings: Settings, event: dict[str, Any]) -> bool:
    """Deliver one event without raising or logging credentials."""
    url = settings.ops_alert_webhook_url.strip()
    token = settings.ops_alert_webhook_token.strip()
    if not url or not token:
        return False
    payload = {
        "source": "rams_runtime",
        "service": "RAMS",
        "environment": "production",
        "occurred_at": datetime.now(UTC).isoformat(),
        **event,
    }
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=settings.ops_alert_timeout_seconds,
        )
        return 200 <= response.status_code < 300
    except httpx.HTTPError as exc:
        logger.warning("RAMS operational alert delivery failed: %s", exc.__class__.__name__)
        return False
