"""Orchestrator tests ensure evidence failures never authorise dispatch."""
from unittest.mock import Mock

import pytest

from repo_mgmt.incident_identity import IncidentIdentity
from repo_mgmt.recovery_orchestrator import RecoveryOrchestrator


def identity():
    return IncidentIdentity(
        repository="Jonathan-Harris1975/RAMS", category="ci_failure",
        source="github_actions", fingerprint="job:lint", head_sha="a" * 40,
    )


def test_claim_requires_d1_and_writes_r2():
    coordinator, r2 = Mock(), Mock()
    coordinator.acquire.return_value = True
    orchestrator = RecoveryOrchestrator(coordinator, r2)
    assert orchestrator.claim(identity(), owner="worker", diagnosis={"step": "lint"}, now=100)
    assert r2.put_object.call_count == 1
    assert r2.put_object.call_args.args[0] == "hive-repositories"
    assert r2.put_object.call_args.args[1].startswith("autonomous-recovery/RAMS/incidents/")


def test_rejected_claim_does_not_write():
    coordinator, r2 = Mock(), Mock()
    coordinator.acquire.return_value = False
    assert not RecoveryOrchestrator(coordinator, r2).claim(
        identity(), owner="worker", diagnosis={}, now=100)
    r2.put_object.assert_not_called()


def test_evidence_failure_is_fail_closed():
    coordinator, r2 = Mock(), Mock()
    coordinator.acquire.return_value = True
    r2.put_object.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        RecoveryOrchestrator(coordinator, r2).claim(
            identity(), owner="worker", diagnosis={}, now=100)
    coordinator.finish.assert_not_called()


def test_finish_requires_receipt_before_releasing():
    coordinator, r2 = Mock(), Mock()
    coordinator.finish.return_value = True
    assert RecoveryOrchestrator(coordinator, r2).finish(
        identity(), owner="worker", resolved=True,
        evidence={"checks": "green"}, now=200)
    assert coordinator.finish.call_count == 1
    assert r2.put_object.call_args.args[1].startswith("autonomous-recovery/RAMS/receipts/")
