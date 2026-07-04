"""
Configuration Manager for the RAMS Optimisation Subsystem.

All optimisation thresholds, tier boundaries, category toggles, and rollback
behaviour live in an external JSON policy file (default:
``config/optimisation_policy.json``), never as Python literals. This module
loads, validates, and exposes that policy as a typed object.

Override the policy location with the ``RMS_OPTIMISATION_POLICY_PATH``
environment variable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from repo_mgmt.optimisation.models import ConfidenceTier, OptimisationCategory

_DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "optimisation_policy.json"
)
_ENV_VAR = "RMS_OPTIMISATION_POLICY_PATH"

_TIER_ORDER: dict[ConfidenceTier, int] = {
    "observe": 0,
    "recommend": 1,
    "auto_configure": 2,
    "patch_candidate": 3,
}


class PolicyConfigurationError(Exception):
    """Raised when the optimisation policy file is missing or invalid."""


class ConfidenceTierBand(BaseModel):
    """One inclusive-exclusive confidence band, e.g. 70 <= score < 90."""

    model_config = ConfigDict(extra="forbid")

    name: ConfidenceTier
    min: float = Field(ge=0.0, le=100.0)
    max: float = Field(ge=0.0, le=100.0)

    @model_validator(mode="after")
    def _min_below_max(self) -> "ConfidenceTierBand":
        if self.min >= self.max:
            raise ValueError(f"tier {self.name!r} has min >= max")
        return self


class ConfidenceWeights(BaseModel):
    """Relative weights used to compute a confidence score. Must sum to 1.0."""

    model_config = ConfigDict(extra="forbid")

    evidence_strength: float = Field(ge=0.0, le=1.0)
    sample_size: float = Field(ge=0.0, le=1.0)
    recurrence: float = Field(ge=0.0, le=1.0)
    severity: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "ConfidenceWeights":
        total = self.evidence_strength + self.sample_size + self.recurrence + self.severity
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"confidence_weights must sum to 1.0, got {total}")
        return self


class TrendAnalysisPolicy(BaseModel):
    """Guardrails preventing optimisation from a single anomaly."""

    model_config = ConfigDict(extra="forbid")

    min_audit_cycles: int = Field(ge=1)
    min_distinct_evidence_samples: int = Field(ge=1)
    evidence_window_days: int = Field(ge=1)
    single_anomaly_max_confidence: float = Field(ge=0.0, le=100.0)


class OscillationPolicy(BaseModel):
    """Guardrails preventing repeated auto-configure flip-flopping on one signal.

    These are separate from ``TrendAnalysisPolicy``: trend analysis governs
    whether a *first* auto-configure action is justified at all (recurrence
    across audit cycles); this governs whether *another* one is allowed to
    run so soon after the last one for the same signature.

    Optional with defaults so existing policy files that predate this guard
    continue to load unchanged; the defaults (24h cooldown, look at the last
    4 finished experiments for flip-flopping) are conservative starting
    points, not tuned production values.
    """

    model_config = ConfigDict(extra="forbid")

    min_reoptimisation_interval_hours: float = Field(ge=0.0, default=24.0)
    reversal_lookback: int = Field(ge=0, default=4)


class CategoryPolicy(BaseModel):
    """Per-category enable flag and the highest tier allowed without review."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    max_tier_without_review: ConfidenceTier


class RollbackPolicy(BaseModel):
    """Rollback manager behaviour."""

    model_config = ConfigDict(extra="forbid")

    verify_timeout_seconds: int = Field(ge=1)
    auto_rollback_on_verification_failure: bool
    keep_snapshots: int = Field(ge=1)


class PatchGeneratorPolicy(BaseModel):
    """Requirements a patch candidate must satisfy before it may be emitted."""

    model_config = ConfigDict(extra="forbid")

    require_tests: bool
    require_lint: bool
    require_regression: bool
    require_acceptance_criteria: bool
    require_rollback_package: bool
    max_files: int = Field(ge=1)
    max_changes: int = Field(ge=1)


class HistoryPolicy(BaseModel):
    """History retention behaviour."""

    model_config = ConfigDict(extra="forbid")

    retention_days: int = Field(ge=1)


class OptimisationPolicy(BaseModel):
    """The fully validated, externally configured optimisation policy."""

    model_config = ConfigDict(extra="forbid")

    version: int
    confidence_tiers: list[ConfidenceTierBand]
    confidence_weights: ConfidenceWeights
    trend_analysis: TrendAnalysisPolicy
    oscillation: OscillationPolicy = Field(default_factory=OscillationPolicy)
    categories: dict[OptimisationCategory, CategoryPolicy]
    rollback: RollbackPolicy
    patch_generator: PatchGeneratorPolicy
    history: HistoryPolicy

    @field_validator("confidence_tiers")
    @classmethod
    def _tiers_cover_0_to_100_without_gaps(
        cls, value: list[ConfidenceTierBand]
    ) -> list[ConfidenceTierBand]:
        ordered = sorted(value, key=lambda band: band.min)
        if not ordered or ordered[0].min != 0:
            raise ValueError("confidence_tiers must start at 0")
        if ordered[-1].max != 100:
            raise ValueError("confidence_tiers must end at 100")
        for previous, current in zip(ordered, ordered[1:]):
            if previous.max != current.min:
                raise ValueError(
                    f"confidence_tiers must be contiguous: {previous.name} ends at "
                    f"{previous.max}, {current.name} starts at {current.min}"
                )
        names = {band.name for band in ordered}
        if names != set(_TIER_ORDER):
            raise ValueError(f"confidence_tiers must define exactly {set(_TIER_ORDER)}")
        return ordered

    def tier_for_score(self, score: float) -> ConfidenceTier:
        """Return the confidence tier a score falls into."""
        clamped = max(0.0, min(100.0, score))
        for band in self.confidence_tiers:
            if band.min <= clamped < band.max or (band.max == 100.0 and clamped == 100.0):
                return band.name
        raise PolicyConfigurationError(f"no confidence tier covers score {score}")

    def max_allowed_tier(self, category: OptimisationCategory) -> ConfidenceTier:
        """Return the highest tier a category may reach without manual review."""
        policy = self.categories.get(category)
        if policy is None or not policy.enabled:
            return "observe"
        return policy.max_tier_without_review

    def is_category_enabled(self, category: OptimisationCategory) -> bool:
        policy = self.categories.get(category)
        return bool(policy and policy.enabled)

    def effective_tier(self, category: OptimisationCategory, raw_tier: ConfidenceTier) -> ConfidenceTier:
        """Cap a raw confidence tier at the category's configured ceiling.

        This is what stops, e.g., a 99-confidence "scheduler" finding from
        silently becoming a patch candidate when policy caps that category at
        "recommend" -- the action is still recorded at its true confidence
        score, but routing treats it as the capped tier.
        """
        if not self.is_category_enabled(category):
            return "observe"
        ceiling = self.max_allowed_tier(category)
        if _TIER_ORDER[raw_tier] > _TIER_ORDER[ceiling]:
            return ceiling
        return raw_tier


def load_policy(path: str | Path | None = None) -> OptimisationPolicy:
    """Load and validate the optimisation policy from disk.

    Resolution order: explicit ``path`` argument, then the
    ``RMS_OPTIMISATION_POLICY_PATH`` environment variable, then the bundled
    default at ``config/optimisation_policy.json``.
    """
    resolved = Path(path) if path else Path(os.environ.get(_ENV_VAR, "") or _DEFAULT_POLICY_PATH)
    if not resolved.exists():
        raise PolicyConfigurationError(f"optimisation policy file not found: {resolved}")
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PolicyConfigurationError(f"optimisation policy file is not valid JSON: {exc}") from exc
    # Top-level metadata keys are documentation only and are not part of the
    # strict schema (which forbids unknown fields so that a typo'd threshold
    # name fails loudly instead of being silently ignored).
    raw.pop("$schema", None)
    raw.pop("description", None)
    try:
        return OptimisationPolicy.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError, re-raised as our own type
        raise PolicyConfigurationError(f"optimisation policy failed validation: {exc}") from exc
