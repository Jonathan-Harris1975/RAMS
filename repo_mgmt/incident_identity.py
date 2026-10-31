"""Deterministic incident identity for deduplicating autonomous repair work.

Pure functions: no network access, secrets, or mutable process-global state.
Persistence/lease ownership must be supplied by a separate durable store.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_KIND = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


@dataclass(frozen=True)
class IncidentIdentity:
    repository: str
    category: str
    source: str
    fingerprint: str
    head_sha: str

    def __post_init__(self) -> None:
        if not _REPO.fullmatch(self.repository) or any(\n            segment in {".", ".."} for segment in self.repository.split("/")\n        ):
            raise ValueError("repository must be owner/name")
        if not _KIND.fullmatch(self.category):
            raise ValueError("invalid incident category")
        if not _KIND.fullmatch(self.source):
            raise ValueError("invalid incident source")
        if not _SHA.fullmatch(self.head_sha):
            raise ValueError("head_sha must be a full commit SHA")
        if not self.fingerprint or len(self.fingerprint) > 256:
            raise ValueError("fingerprint must be 1-256 characters")

    @property
    def incident_key(self) -> str:
        """Stable across commits for the same underlying failure."""
        return _digest((self.repository.lower(), self.category, self.source, self.fingerprint))

    @property
    def attempt_key(self) -> str:
        """Exact-head attempt key; a new commit requires new validation."""
        return _digest((self.incident_key, self.head_sha.lower()))


def _digest(parts: tuple[str, ...]) -> str:
    payload = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
