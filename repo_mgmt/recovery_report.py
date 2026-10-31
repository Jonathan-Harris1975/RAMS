"""Read-only, bounded incident status reporting from the durable D1 coordinator."""
from __future__ import annotations

import re
from typing import Any

from repo_mgmt.d1_coordinator import D1Coordinator

_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")


def recovery_summary(coordinator: D1Coordinator, repository: str, *,
                     limit: int = 50) -> dict[str, Any]:
    """Return recent incidents and retry counts without secrets or evidence bodies.

    This endpoint-independent adapter is safe for human-readable HIVE reporting.
    Its data is observational: a resolved ledger state alone never proves a
    production deployment or that the original defect stopped recurring.
    """
    if not _REPOSITORY.fullmatch(repository) or any(
        part in {".", ".."} for part in repository.split("/")
    ):
        raise ValueError("invalid repository")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
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
    allowed = {"incident_key", "attempt_key", "head_sha", "state",
               "attempts", "lease_until", "updated_at"}
    return {
        "repository": repository,
        "total_returned": len(rows),
        "active": sum(row.get("state") == "repairing" for row in rows),
        "resolved": sum(row.get("state") == "resolved" for row in rows),
        "retry_attempts": sum(max(0, row["attempts"] - 1) for row in rows),
        "incidents": [{key: row[key] for key in allowed if key in row} for row in rows],
    }
