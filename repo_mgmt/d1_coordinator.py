"""D1 transactional coordinator API client.

Use only from trusted runtime with a scoped D1 token. SQL must be a
single atomic conditional statement for claims; never SELECT then UPDATE.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

_UUID = re.compile(r"^[0-9a-f-]{36}$")
_ACCOUNT = re.compile(r"^[0-9a-f]{32}$")


class D1CoordinationError(RuntimeError):
    pass


class D1Coordinator:
    def __init__(self, account_id: str, database_id: str, token: str) -> None:
        if not _ACCOUNT.fullmatch(account_id) or not _UUID.fullmatch(database_id):
            raise ValueError("invalid D1 resource identifiers")
        if not token or any(c.isspace() for c in token):
            raise ValueError("invalid D1 token")
        self._url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/d1/database/{database_id}/query"
        )
        self._token = token

    def query(self, sql: str, params: list[str | int] | None = None) -> list[dict[str, Any]]:
        """Execute one parameterised statement and reject ambiguous results."""
        if not sql or ";" in sql.rstrip().rstrip(";") or len(sql) > 8192:
            raise ValueError("expected one bounded SQL statement")
        payload = json.dumps({"sql": sql, "params": params or []}).encode()
        request = urllib.request.Request(
            self._url, data=payload, method="POST",
            headers={"Authorization": f"Bearer {self._token}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:  # nosec B310 - fixed HTTPS Cloudflare API URL
                data = json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise D1CoordinationError("D1 coordination request failed or outcome uncertain") from exc
        if not data.get("success") or not isinstance(data.get("result"), list):
            raise D1CoordinationError("D1 coordination rejected the query")
        results = data["result"]
        if len(results) != 1 or not results[0].get("success"):
            raise D1CoordinationError("D1 coordination statement did not succeed")
        rows = results[0].get("results")
        if not isinstance(rows, list):
            raise D1CoordinationError("D1 returned invalid results")
        return rows

    def acquire(
        self, incident_key: str, attempt_key: str, repository: str,
        head_sha: str, owner: str, *, now: int, lease_seconds: int = 300,
        max_attempts: int = 3,
    ) -> bool:
        """Atomically claim or reclaim a lease on the exact attempt.

        One INSERT ON CONFLICT statement prevents competing workers from
        passing a separate read-before-write check.
        """
        if not re.fullmatch(r"[a-f0-9]{64}", incident_key):
            raise ValueError("invalid incident key")
        if not re.fullmatch(r"[a-f0-9]{64}", attempt_key):
            raise ValueError("invalid attempt key")
        if not re.fullmatch(r"[a-f0-9]{40}", head_sha):
            raise ValueError("invalid head SHA")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", repository):
            raise ValueError("invalid repository")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", owner) or not 1 <= lease_seconds <= 3600 or not 1 <= max_attempts <= 20:
            raise ValueError("invalid claim bounds")
        sql = (
            "INSERT INTO incidents (incident_key, attempt_key, repository, head_sha, owner, "
            "lease_until, attempts, state, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, 'repairing', ?) "
            "ON CONFLICT(incident_key) DO UPDATE SET "
            "attempt_key=excluded.attempt_key, repository=excluded.repository, head_sha=excluded.head_sha, "
            "owner=excluded.owner, lease_until=excluded.lease_until, "
            "attempts=CASE WHEN incidents.attempt_key=excluded.attempt_key "
            "THEN incidents.attempts+1 ELSE 1 END, state='repairing', "
            "updated_at=excluded.updated_at WHERE "
            "(incidents.owner IS NULL OR incidents.lease_until<=?) "
            "AND NOT (incidents.state='resolved' AND incidents.attempt_key=excluded.attempt_key) "
            "AND (incidents.attempt_key!=excluded.attempt_key OR incidents.attempts<?) "
            "RETURNING incident_key"
        )
        rows = self.query(sql, [incident_key, attempt_key, repository, head_sha,
                                owner, now + lease_seconds, now,
                                now, max_attempts])
        return len(rows) == 1 and rows[0].get("incident_key") == incident_key

    def finish(self, incident_key: str, attempt_key: str, owner: str, *,
               resolved: bool, now: int) -> bool:
        """Release only a live lease belonging to this owner and exact attempt."""
        if not re.fullmatch(r"[a-f0-9]{64}", incident_key) or not re.fullmatch(r"[a-f0-9]{64}", attempt_key):
            raise ValueError("invalid incident identity")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", owner):
            raise ValueError("invalid owner")
        rows = self.query(
            "UPDATE incidents SET owner=NULL, lease_until=0, state=?, updated_at=? "
            "WHERE incident_key=? AND attempt_key=? AND owner=? AND lease_until>? "
            "RETURNING incident_key",
            ["resolved" if resolved else "pending", now, incident_key,
             attempt_key, owner, now],
        )
        return len(rows) == 1 and rows[0].get("incident_key") == incident_key

    def renew(self, incident_key: str, attempt_key: str, owner: str, *,
              now: int, lease_seconds: int = 300) -> bool:
        """Renew only an unexpired lease owned by the same exact attempt."""
        if not re.fullmatch(r"[a-f0-9]{64}", incident_key) or not re.fullmatch(r"[a-f0-9]{64}", attempt_key):
            raise ValueError("invalid incident identity")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", owner) or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid lease renewal")
        rows = self.query(
            "UPDATE incidents SET lease_until=?, updated_at=? "
            "WHERE incident_key=? AND attempt_key=? AND owner=? AND lease_until>? "
            "RETURNING incident_key",
            [now + lease_seconds, now, incident_key,
             attempt_key, owner, now],
        )
        return len(rows) == 1 and rows[0].get("incident_key") == incident_key

    def record_event(self, incident_key: str, attempt_key: str, owner: str,
                     event: str, *, now: int) -> bool:
        """Append a bounded, durable D1 audit event."""
        if not re.fullmatch(r"[a-f0-9]{64}", incident_key) or not re.fullmatch(r"[a-f0-9]{64}", attempt_key):
            raise ValueError("invalid incident identity")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", owner) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", event):
            raise ValueError("invalid audit event")
        rows = self.query(
            "INSERT INTO incident_events (incident_key,attempt_key,event,owner,occurred_at) "
            "VALUES (?,?,?,?,?) RETURNING id",
            [incident_key, attempt_key, event, owner, now],
        )
        return len(rows) == 1 and isinstance(rows[0].get("id"), int)
