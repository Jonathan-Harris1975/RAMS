"""Durable incident ledger contract tests."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from repo_mgmt.incident_identity import IncidentIdentity
from repo_mgmt.incident_ledger import IncidentLedger


def incident(sha="a" * 40):
    return IncidentIdentity(
        repository="Jonathan-Harris1975/RAMS",
        category="ci_failure",
        source="github_actions",
        fingerprint="run:lint",
        head_sha=sha,
    )


def test_claim_deduplication_and_exact_owner(tmp_path):
    ledger = IncidentLedger(tmp_path / "incidents.sqlite3")
    key = incident()
    assert ledger.acquire(key, owner="worker-a", now=100)
    assert not ledger.acquire(key, owner="worker-b", now=101)
    assert not ledger.finish(key, owner="worker-b", resolved=True, now=102)
    assert ledger.finish(key, owner="worker-a", resolved=True, now=103)
    assert not ledger.acquire(key, owner="worker-b", now=104)
    assert ledger.status(key)["state"] == "resolved"


def test_expired_lease_and_retry_budget(tmp_path):
    ledger = IncidentLedger(tmp_path / "incidents.sqlite3")
    key = incident()
    assert ledger.acquire(key, owner="a", now=100, lease_seconds=10, max_attempts=2)
    assert not ledger.acquire(key, owner="b", now=109, max_attempts=2)
    assert ledger.acquire(key, owner="b", now=110, max_attempts=2)
    assert not ledger.finish(key, owner="a", resolved=True, now=111)
    assert not ledger.acquire(key, owner="c", now=500, max_attempts=2)
    assert ledger.status(key)["attempts"] == 2


def test_new_head_resets_budget_after_expiry(tmp_path):
    ledger = IncidentLedger(tmp_path / "incidents.sqlite3")
    old = incident()
    new = incident("b" * 40)
    assert ledger.acquire(old, owner="a", now=100, lease_seconds=10, max_attempts=1)
    assert ledger.acquire(new, owner="b", now=111, max_attempts=1)
    assert ledger.status(new)["attempt_key"] == new.attempt_key
    assert ledger.status(new)["attempts"] == 1
    assert not ledger.finish(old, owner="a", resolved=True, now=112)


def test_parallel_claims_have_single_owner(tmp_path):
    ledger = IncidentLedger(tmp_path / "incidents.sqlite3")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda i: ledger.acquire(incident(), owner=f"worker-{i}", now=100),
            range(8),
        ))
    assert results.count(True) == 1


def test_invalid_bounds_and_memory_rejected(tmp_path):
    with pytest.raises(ValueError):
        IncidentLedger(":memory:")
    ledger = IncidentLedger(tmp_path / "incidents.sqlite3")
    with pytest.raises(ValueError):
        ledger.acquire(incident(), owner="", now=100)
    with pytest.raises(ValueError):
        ledger.acquire(incident(), owner="a", max_attempts=0, now=100)


def test_lease_renewal_is_fenced_by_owner_and_expiry(tmp_path):
    ledger = IncidentLedger(tmp_path / "incidents.sqlite3")
    key = incident()
    assert ledger.acquire(key, owner="a", now=100, lease_seconds=10)
    assert not ledger.renew(key, owner="b", now=101)
    assert ledger.renew(key, owner="a", now=105, lease_seconds=20)
    assert not ledger.acquire(key, owner="b", now=124)
    assert not ledger.renew(key, owner="a", now=126)
    assert ledger.acquire(key, owner="b", now=126)
    assert not ledger.finish(key, owner="a", resolved=True, now=127)
