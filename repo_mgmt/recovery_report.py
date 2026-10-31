"""Read-only, bounded incident status reporting from the durable D1 coordinator."""
from __future__ import annotations

import re
import time
from typing import Any

from repo_mgmt.d1_coordinator import D1Coordinator

_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")


def recovery_summary(coordinator: D1Coordinator, repository: str, *,
                     limit: int = 50, now: int | None = None) -> dict[str, Any]:
    """Return recent incidents and retry counts without secrets or evidence bodies.

    This endpoint-independent adapter is safe for human-readable HIVE reporting.
    Its data is observational: a resolved ledger state alone never proves a
    production deployment or that the original defect stopped recurring.
    """
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository) or any(
        part in {".", ".."} for part in repository.split("/")
    ):
        raise ValueError("invalid repository")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    timestamp = int(time.time()) if now is None else now
    if type(timestamp) is not int or timestamp < 0:
        raise ValueError("invalid report timestamp")
    rows = coordinator.query(
        "SELECT incident_key, attempt_key, head_sha, state, attempts, "
        "lease_until, updated_at FROM incidents WHERE repository=? "
        "ORDER BY updated_at DESC LIMIT ?",
        [repository, limit],
    )
    if not isinstance(rows, list) or len(rows) > limit or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid D1 incident report response")
    for row in rows:
        attempts = row.get("attempts")
        if isinstance(attempts, bool) or not isinstance(attempts, int) or not 0 <= attempts <= 1000000:
            raise ValueError("invalid D1 incident retry count")
    events = coordinator.query(
        "SELECT event, COUNT(*) AS total FROM incident_events "
        "WHERE incident_key IN (SELECT incident_key FROM incidents WHERE repository=?) "
        "GROUP BY event ORDER BY event LIMIT 100",
        [repository],
    )
    if not isinstance(events, list) or len(events) > 100:
        raise ValueError("invalid D1 recovery event report")
    event_totals = {}
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("event"), str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", event["event"]):
            raise ValueError("invalid D1 recovery event")
        total = event.get("total")
        if type(total) is not int or not 0 <= total <= 1_000_000:
            raise ValueError("invalid D1 recovery event total")
        event_totals[event["event"]] = total
    allowed = {"incident_key", "attempt_key", "head_sha", "state",
               "attempts", "lease_until", "updated_at"}
    for row in rows:
        for field, width in (("incident_key", 64), ("attempt_key", 64), ("head_sha", 40)):
            value = row.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{" + str(width) + r"}", value):
                raise ValueError("invalid D1 incident identity")
        if row.get("state") not in {"pending", "repairing", "handed_off", "resolved"}:
            raise ValueError("invalid D1 incident state")
        for field in ("lease_until", "updated_at"):
            if type(row.get(field)) is not int or row[field] < 0:
                raise ValueError("invalid D1 incident timestamp")
    return {
        "repository": repository,
        "total_returned": len(rows),
        "active": sum(row.get("state") in {"repairing", "handed_off"} for row in rows),
        "handed_off": sum(row["state"] == "handed_off" for row in rows),
        "resolved": sum(row.get("state") == "resolved" for row in rows),
        "expired_leases": sum(row["state"] == "repairing" and
                              row["lease_until"] <= timestamp for row in rows),
        "retry_exhausted": sum(row["state"] != "resolved" and row["attempts"] >= 3
                               for row in rows),
        "retry_attempts": sum(max(0, row["attempts"] - 1) for row in rows),
        "event_totals": event_totals,
        "incidents": [{key: row[key] for key in allowed if key in row} for row in rows],
    }
