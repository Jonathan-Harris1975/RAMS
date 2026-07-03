"""
Trend Analysis for the RAMS Optimisation Subsystem.

This is the module that stops RAMS from "optimising" based on a single
anomaly. New evidence is ingested into the append-only Optimisation History
keyed by its signature (pipeline + category + signal). A signal only becomes
eligible for confidence scoring once it has recurred across at least
``policy.trend_analysis.min_audit_cycles`` distinct audit runs *and* across
at least ``policy.trend_analysis.min_distinct_evidence_samples`` samples --
both configured externally, never hard-coded.

Evidence outside the configured recency window is ignored, so a signal that
stopped recurring months ago does not keep counting toward the threshold
forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from repo_mgmt.optimisation.history import OptimisationHistoryStore
from repo_mgmt.optimisation.models import AuditEvidence
from repo_mgmt.optimisation.policy import OptimisationPolicy


@dataclass(frozen=True)
class TrendSignal:
    """A signature with enough accumulated evidence to be worth scoring."""

    signature: str
    pipeline: str
    category: str
    signal: str
    distinct_cycles: int
    distinct_audit_ids: tuple[str, ...]
    evidence: tuple[AuditEvidence, ...] = field(default_factory=tuple)

    @property
    def is_eligible_marker(self) -> bool:
        """True once trend_analysis has already established eligibility."""
        return True


class TrendAnalyser:
    """Ingests evidence and surfaces only signals with repeated recurrence."""

    def __init__(self, policy: OptimisationPolicy, history: OptimisationHistoryStore) -> None:
        self._policy = policy
        self._history = history

    def ingest(self, evidence: AuditEvidence) -> None:
        """Record one piece of evidence in the durable history log.

        This does not, by itself, produce an optimisation action -- it only
        accumulates evidence. Call ``evaluate`` to check eligibility.
        """
        self._history.append(
            evidence.pipeline,
            {
                "type": "evidence",
                "signature": evidence.signature,
                **evidence.model_dump(mode="json"),
            },
        )

    def evaluate(self, pipeline: str, signature: str) -> TrendSignal | None:
        """Return a TrendSignal if ``signature`` has recurred enough to act on.

        Returns ``None`` (not zero-confidence) when the signal has not yet
        met the recurrence bar -- callers must treat "not enough evidence
        yet" as categorically different from "evidence says nothing is
        wrong".
        """
        cfg = self._policy.trend_analysis
        cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.evidence_window_days)

        records = self._history.query(pipeline, record_type="evidence", signature=signature)
        known_fields = set(AuditEvidence.model_fields)
        recent: list[AuditEvidence] = []
        for record in records:
            observed_at = record.get("observed_at")
            try:
                ts = datetime.fromisoformat(str(observed_at))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if ts < cutoff:
                continue
            # History records carry envelope keys ("type", "signature") in
            # addition to the AuditEvidence fields; AuditEvidence forbids
            # extra fields by design, so strip the envelope before
            # reconstructing the strict model.
            evidence_fields = {key: value for key, value in record.items() if key in known_fields}
            recent.append(AuditEvidence.model_validate(evidence_fields))

        if not recent:
            return None

        distinct_audit_ids = sorted({item.audit_id for item in recent})
        # "Distinct cycles" is audit-run recurrence, the thing single-anomaly
        # protection cares about -- not raw evidence-record count, which
        # could be inflated by one noisy audit run emitting many samples.
        distinct_cycles = len(distinct_audit_ids)
        distinct_samples = sum(item.sample_size for item in recent)

        if distinct_cycles < cfg.min_audit_cycles:
            return None
        if distinct_samples < cfg.min_distinct_evidence_samples:
            return None

        first = recent[0]
        return TrendSignal(
            signature=signature,
            pipeline=pipeline,
            category=first.category,
            signal=first.signal,
            distinct_cycles=distinct_cycles,
            distinct_audit_ids=tuple(distinct_audit_ids),
            evidence=tuple(recent),
        )

    def known_signatures(self, pipeline: str) -> set[str]:
        """Return every evidence signature ever ingested for a pipeline."""
        return {
            record["signature"]
            for record in self._history.query(pipeline, record_type="evidence")
            if "signature" in record
        }
