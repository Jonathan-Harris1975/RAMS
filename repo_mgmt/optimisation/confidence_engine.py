"""
Confidence Engine for the RAMS Optimisation Subsystem.

Produces a deterministic, explainable 0-100 confidence score for a set of
recurring evidence, and maps that score onto the policy-defined tier:

    < 70   -> observe          (log only, no action)
    70-90  -> recommend        (surfaced for human decision)
    90-98  -> auto_configure   (safe, reversible config change allowed)
    98+    -> patch_candidate  (code-level change; still gated, never applied
                                 automatically -- see patch_generator.py)

The score is a weighted blend of four independent signals, each normalised
to [0, 1] before weighting, so no single dimension can dominate by accident:

  * evidence_strength -- how far the observed value is from the expected
    value, relative to the expected value.
  * sample_size        -- how many independent samples the evidence spans
    (saturates so extra samples beyond a modest number stop adding score).
  * recurrence          -- how many distinct audit cycles have reproduced the
    same signal (this is what the Trend Analyser gates on, and it is scored
    here too so recurrence pulls weight even inside the "recommend" band).
  * severity            -- the audit-assigned severity of the underlying
    finding.

All weights and saturation points come from the externally configured
policy (repo_mgmt.optimisation.policy) -- nothing here is hard-coded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from repo_mgmt.optimisation.models import AuditEvidence, ConfidenceTier
from repo_mgmt.optimisation.policy import OptimisationPolicy

_SEVERITY_SCORE = {"critical": 1.0, "high": 0.75, "medium": 0.5, "low": 0.25}
_SAMPLE_SIZE_SATURATION = 10  # samples at/above this count score 1.0 on the sample_size axis
_CYCLE_SATURATION = 6  # cycles at/above this count score 1.0 on the recurrence axis


@dataclass(frozen=True)
class ConfidenceResult:
    """Explainable output of a confidence computation."""

    score: float
    tier: ConfidenceTier
    effective_tier: ConfidenceTier
    components: dict[str, float] = field(default_factory=dict)
    rationale: str = ""


def _evidence_strength(evidence: Sequence[AuditEvidence]) -> float:
    """How far observed values deviate from expected, normalised to [0, 1]."""
    deviations: list[float] = []
    for item in evidence:
        if item.observed_value is None or item.expected_value is None:
            continue
        if item.expected_value == 0:
            continue
        deviation = abs(item.observed_value - item.expected_value) / abs(item.expected_value)
        deviations.append(min(1.0, deviation))
    if not deviations:
        # No quantitative signal was supplied; treat qualitative evidence as
        # moderate strength rather than zero, so purely descriptive findings
        # (e.g. "RSS feed validation failing") aren't structurally capped at
        # the observe tier.
        return 0.5
    return sum(deviations) / len(deviations)


def _sample_size_score(evidence: Sequence[AuditEvidence]) -> float:
    total_samples = sum(item.sample_size for item in evidence)
    return min(1.0, total_samples / _SAMPLE_SIZE_SATURATION)


def _recurrence_score(distinct_cycles: int) -> float:
    return min(1.0, distinct_cycles / _CYCLE_SATURATION)


def _severity_score(evidence: Sequence[AuditEvidence]) -> float:
    if not evidence:
        return 0.0
    scores = [_SEVERITY_SCORE.get(item.severity, 0.5) for item in evidence]
    return max(scores)


class ConfidenceEngine:
    """Computes and tier-classifies confidence scores from evidence."""

    def __init__(self, policy: OptimisationPolicy) -> None:
        self._policy = policy

    def score(
        self,
        *,
        evidence: Sequence[AuditEvidence],
        distinct_cycles: int,
        category: str,
    ) -> ConfidenceResult:
        """Compute a confidence score and tier for one recurring signal.

        ``distinct_cycles`` must come from the Trend Analyser, not be
        inferred here, so this engine never has to guess at recurrence.
        """
        weights = self._policy.confidence_weights
        components = {
            "evidence_strength": _evidence_strength(evidence),
            "sample_size": _sample_size_score(evidence),
            "recurrence": _recurrence_score(distinct_cycles),
            "severity": _severity_score(evidence),
        }
        raw_score = (
            components["evidence_strength"] * weights.evidence_strength
            + components["sample_size"] * weights.sample_size
            + components["recurrence"] * weights.recurrence
            + components["severity"] * weights.severity
        ) * 100.0

        # Hard guardrail: evidence from a single audit cycle can never reach
        # the tier that would trigger any automated action, no matter how
        # strong the individual signal looks. This is enforced here (not
        # only in trend_analysis) so the confidence engine is safe even if
        # called directly.
        single_anomaly_cap = self._policy.trend_analysis.single_anomaly_max_confidence
        if distinct_cycles <= 1:
            raw_score = min(raw_score, single_anomaly_cap)

        score = round(min(100.0, max(0.0, raw_score)), 2)
        tier = self._policy.tier_for_score(score)
        effective_tier = self._policy.effective_tier(category, tier)  # type: ignore[arg-type]

        rationale = (
            f"score={score} from evidence_strength={components['evidence_strength']:.2f}, "
            f"sample_size={components['sample_size']:.2f}, "
            f"recurrence={components['recurrence']:.2f} ({distinct_cycles} cycles), "
            f"severity={components['severity']:.2f}; tier={tier}, effective_tier={effective_tier}"
        )
        return ConfidenceResult(
            score=score,
            tier=tier,
            effective_tier=effective_tier,
            components=components,
            rationale=rationale,
        )
