"""Strict runtime schemas for the RAMS Optimisation Subsystem.

These mirror the style of ``repo_mgmt.schemas``: bounded, strictly validated
data shapes rather than free-form dicts, so malformed evidence or actions
fail fast instead of silently corrupting an optimisation decision.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

OptimisationCategory = Literal[
    "scheduler",
    "validators",
    "prompts",
    "rss",
    "podcasts",
    "platform_weighting",
    "configuration",
]

ConfidenceTier = Literal["observe", "recommend", "auto_configure", "patch_candidate"]

OptimisationOutcome = Literal[
    "pending",
    "applied",
    "verified",
    "rolled_back",
    "rejected",
    "manual_review",
]


def new_id(prefix: str) -> str:
    """Return a short, prefixed, collision-resistant identifier."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def utc_now() -> datetime:
    """Return the current UTC time. Isolated for deterministic testing."""
    return datetime.now(timezone.utc)


class AuditEvidence(BaseModel):
    """One piece of evidence drawn from a single audit run/finding.

    This is the atomic unit the Trend Analysis and Confidence Engine reason
    over. It intentionally carries the source audit id so every downstream
    decision remains traceable back to the raw audit data.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(default_factory=lambda: new_id("ev"))
    audit_id: str
    pipeline: str
    category: OptimisationCategory
    signal: str  # stable signature identifying "the same observation", e.g. "scheduler.retry_backoff_too_aggressive"
    metric: str | None = None
    observed_value: float | None = None
    expected_value: float | None = None
    severity: Literal["critical", "high", "medium", "low"] = "medium"
    sample_size: int = Field(default=1, ge=1)
    detail: str = ""
    observed_at: datetime = Field(default_factory=utc_now)

    @field_validator("audit_id", "pipeline", "signal")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("field must be a non-empty string")
        return value.strip()

    @property
    def signature(self) -> str:
        """Stable key grouping evidence describing the same underlying issue."""
        raw = f"{self.pipeline}:{self.category}:{self.signal}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class OptimisationAction(BaseModel):
    """A single candidate optimisation, produced only from repeated evidence.

    ``action_id`` is deterministic (derived from the evidence signature) so
    re-running trend analysis over the same recurring issue converges on the
    same action rather than spawning duplicates.
    """

    model_config = ConfigDict(extra="forbid")

    action_id: str
    signature: str
    pipeline: str
    category: OptimisationCategory
    signal: str
    description: str
    proposed_change: dict[str, Any] = Field(default_factory=dict)
    supporting_audit_ids: list[str] = Field(default_factory=list)
    supporting_cycles: int = Field(ge=0, default=0)
    confidence_score: float = Field(ge=0.0, le=100.0)
    tier: ConfidenceTier
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("supporting_audit_ids")
    @classmethod
    def _dedup_audit_ids(cls, value: list[str]) -> list[str]:
        return sorted(set(value))
