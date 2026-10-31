"""Transactional SQLite incident ownership and bounded retry ledger.

Use a persistent volume for the database; ephemeral containers cannot provide durable
ownership across redeployments. SQLite serialises competing writers atomically.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from repo_mgmt.incident_identity import IncidentIdentity

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_key TEXT PRIMARY KEY,
    attempt_key TEXT NOT NULL,
    repository TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    owner TEXT,
    lease_until REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending',
    updated_at REAL NOT NULL
);
"""


class IncidentLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("persistent database path required")
        with self._connect() as db:
            db.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.execute("PRAGMA busy_timeout=15000")
        return db

    def acquire(
        self,
        identity: IncidentIdentity,
        *,
        owner: str,
        lease_seconds: int = 300,
        max_attempts: int = 3,
        now: float | None = None,
    ) -> bool:
        """Claim a failed exact-head attempt; never steal an unexpired lease."""
        if not owner or len(owner) > 128:
            raise ValueError("owner must be 1-128 characters")
        if not 1 <= lease_seconds <= 3600 or not 1 <= max_attempts <= 20:
            raise ValueError("invalid recovery bounds")
        timestamp = time.time() if now is None else now
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT attempt_key, owner, lease_until, attempts, state "
                "FROM incidents WHERE incident_key=?",
                (identity.incident_key,),
            ).fetchone()
            if row:
                previous_attempt, current_owner, expiry, attempts, state = row
                if state == "resolved" and previous_attempt == identity.attempt_key:
                    db.execute("COMMIT")
                    return False
                if current_owner and expiry > timestamp:
                    db.execute("COMMIT")
                    return False
                if previous_attempt != identity.attempt_key:
                    attempts = 0
                if attempts >= max_attempts:
                    db.execute("COMMIT")
                    return False
                db.execute(
                    "UPDATE incidents SET attempt_key=?, head_sha=?, owner=?, "
                    "lease_until=?, attempts=?, state='repairing', updated_at=? "
                    "WHERE incident_key=?",
                    (identity.attempt_key, identity.head_sha.lower(), owner,
                     timestamp + lease_seconds, attempts + 1, timestamp,
                     identity.incident_key),
                )
            else:
                db.execute(
                    "INSERT INTO incidents (incident_key, attempt_key, repository, "
                    "head_sha, owner, lease_until, attempts, state, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, 'repairing', ?)",
                    (identity.incident_key, identity.attempt_key,
                     identity.repository, identity.head_sha.lower(), owner,
                     timestamp + lease_seconds, timestamp),
                )
            db.execute("COMMIT")
            return True

    def renew(
        self, identity: IncidentIdentity, *, owner: str,
        lease_seconds: int = 300, now: float | None = None,
    ) -> bool:
        """Extend an active lease without changing its owner or attempt."""
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid lease duration")
        timestamp = time.time() if now is None else now
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE incidents SET lease_until=?, updated_at=? "
                "WHERE incident_key=? AND attempt_key=? AND owner=? "
                "AND lease_until>?",
                (timestamp + lease_seconds, timestamp, identity.incident_key,
                 identity.attempt_key, owner, timestamp),
            )
            return cursor.rowcount == 1

    def finish(
        self, identity: IncidentIdentity, *, owner: str, resolved: bool,
        now: float | None = None,
    ) -> bool:
        """Only the active exact-head owner may complete an attempt."""
        timestamp = time.time() if now is None else now
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE incidents SET owner=NULL, lease_until=0, state=?, "
                "updated_at=? WHERE incident_key=? AND attempt_key=? "
                "AND owner=? AND lease_until>?",
                ("resolved" if resolved else "pending", timestamp,
                 identity.incident_key, identity.attempt_key, owner, timestamp),
            )
            return cursor.rowcount == 1

    def status(self, identity: IncidentIdentity) -> dict[str, object] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT attempt_key, head_sha, owner, lease_until, attempts, "
                "state FROM incidents WHERE incident_key=?",
                (identity.incident_key,),
            ).fetchone()
        if row is None:
            return None
        return dict(zip(
            ("attempt_key", "head_sha", "owner", "lease_until", "attempts", "state"),
            row, strict=True,
        ))
