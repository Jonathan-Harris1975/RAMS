"""Tests for repo_mgmt.optimisation.trend_analysis — the single-anomaly guard."""

from __future__ import annotations

from repo_mgmt.optimisation.history import OptimisationHistoryStore
from repo_mgmt.optimisation.models import AuditEvidence
from repo_mgmt.optimisation.policy import load_policy
from repo_mgmt.optimisation.trend_analysis import TrendAnalyser


def _make_analyser(tmp_path):
    policy = load_policy()
    history = OptimisationHistoryStore(tmp_path / "history")
    return TrendAnalyser(policy, history), history


def _evidence(audit_id: str, **overrides) -> AuditEvidence:
    defaults = dict(
        audit_id=audit_id,
        pipeline="seo-aeo-geo",
        category="scheduler",
        signal="retry_backoff_too_aggressive",
        observed_value=9.0,
        expected_value=3.0,
        sample_size=4,
    )
    defaults.update(overrides)
    return AuditEvidence(**defaults)


def test_single_evidence_record_is_not_eligible(tmp_path) -> None:
    analyser, _ = _make_analyser(tmp_path)
    ev = _evidence("audit-1")
    analyser.ingest(ev)
    signal = analyser.evaluate("seo-aeo-geo", ev.signature)
    assert signal is None


def test_two_cycles_below_configured_minimum_still_not_eligible(tmp_path) -> None:
    analyser, _ = _make_analyser(tmp_path)
    ev1 = _evidence("audit-1")
    ev2 = _evidence("audit-2")
    analyser.ingest(ev1)
    analyser.ingest(ev2)
    signal = analyser.evaluate("seo-aeo-geo", ev1.signature)
    assert signal is None  # default policy requires min_audit_cycles == 3


def test_three_distinct_cycles_becomes_eligible(tmp_path) -> None:
    analyser, _ = _make_analyser(tmp_path)
    evs = [_evidence(f"audit-{i}") for i in range(3)]
    for ev in evs:
        analyser.ingest(ev)
    signal = analyser.evaluate("seo-aeo-geo", evs[0].signature)
    assert signal is not None
    assert signal.distinct_cycles == 3
    assert len(signal.evidence) == 3


def test_same_audit_id_repeated_does_not_count_as_multiple_cycles(tmp_path) -> None:
    """Re-ingesting the same audit run's evidence must not fake recurrence."""
    analyser, _ = _make_analyser(tmp_path)
    ev = _evidence("audit-1")
    for _ in range(5):
        analyser.ingest(ev)
    signal = analyser.evaluate("seo-aeo-geo", ev.signature)
    assert signal is None


def test_different_signatures_are_tracked_independently(tmp_path) -> None:
    analyser, _ = _make_analyser(tmp_path)
    evs_a = [_evidence(f"audit-{i}", signal="signal_a") for i in range(3)]
    evs_b = [_evidence(f"audit-{i}", signal="signal_b") for i in range(2)]
    for ev in evs_a + evs_b:
        analyser.ingest(ev)
    signal_a = analyser.evaluate("seo-aeo-geo", evs_a[0].signature)
    signal_b = analyser.evaluate("seo-aeo-geo", evs_b[0].signature)
    assert signal_a is not None
    assert signal_b is None  # only 2 cycles, below the minimum of 3


def test_known_signatures_lists_every_ingested_signature(tmp_path) -> None:
    analyser, _ = _make_analyser(tmp_path)
    ev_a = _evidence("audit-1", signal="signal_a")
    ev_b = _evidence("audit-1", signal="signal_b")
    analyser.ingest(ev_a)
    analyser.ingest(ev_b)
    signatures = analyser.known_signatures("seo-aeo-geo")
    assert ev_a.signature in signatures
    assert ev_b.signature in signatures
