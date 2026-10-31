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
    assert coordinator.record_event.call_args.args[3] == "claim_acquired"
    assert r2.put_object_if_absent.call_count == 1
    assert r2.put_object_if_absent.call_args.args[0] == "hive-repositories"
    assert r2.put_object_if_absent.call_args.args[1].startswith("autonomous-recovery/RAMS/incidents/")


def test_rejected_claim_does_not_write():
    coordinator, r2 = Mock(), Mock()
    coordinator.acquire.return_value = False
    assert not RecoveryOrchestrator(coordinator, r2).claim(
        identity(), owner="worker", diagnosis={}, now=100)
    r2.put_object_if_absent.assert_not_called()


def test_evidence_failure_is_fail_closed():
    coordinator, r2 = Mock(), Mock()
    coordinator.acquire.return_value = True
    r2.put_object_if_absent.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        RecoveryOrchestrator(coordinator, r2).claim(
            identity(), owner="worker", diagnosis={}, now=100)
    coordinator.finish.assert_not_called()


def test_finish_requires_receipt_before_releasing():
    coordinator, r2 = Mock(), Mock()
    coordinator.finish.return_value = True
    assert RecoveryOrchestrator(coordinator, r2).finish(
        identity(), owner="worker", resolved=True,
        evidence={"checks": "green", "deployment": {
            "sha": "a" * 40, "environment": "production", "status": "success",
            "provider": "koyeb", "deployment_id": "deploy-123"}}, now=200)
    assert coordinator.finish.call_count == 1
    assert coordinator.record_event.call_args.args[3] == "repair_resolved"
    assert r2.put_object_if_absent.call_args.args[1].startswith("autonomous-recovery/RAMS/receipts/")


def test_audit_failure_prevents_successful_claim():
    coordinator, r2 = Mock(), Mock()
    coordinator.acquire.return_value = True
    coordinator.record_event.side_effect = RuntimeError("audit unavailable")
    with pytest.raises(RuntimeError, match="audit unavailable"):
        RecoveryOrchestrator(coordinator, r2).claim(
            identity(), owner="worker", diagnosis={}, now=100)


def test_heartbeat_requires_d1_renewal_and_audits_success():
    coordinator, r2 = Mock(), Mock()
    coordinator.renew.return_value = True
    orchestrator = RecoveryOrchestrator(coordinator, r2)
    assert orchestrator.renew(identity(), owner="worker", now=150)
    assert coordinator.record_event.call_args.args[3] == "lease_renewed"
    coordinator.renew.return_value = False
    coordinator.record_event.reset_mock()
    assert not orchestrator.renew(identity(), owner="worker", now=151)
    coordinator.record_event.assert_not_called()


@pytest.mark.parametrize("operation", ["claim", "renew", "finish"])
def test_false_audit_acknowledgement_fails_closed(operation):
    coordinator, r2 = Mock(), Mock()
    coordinator.acquire.return_value = True
    coordinator.renew.return_value = True
    coordinator.finish.return_value = True
    coordinator.record_event.return_value = False
    orchestrator = RecoveryOrchestrator(coordinator, r2)
    with pytest.raises(RuntimeError, match="audit was not persisted"):
        if operation == "claim":
            orchestrator.claim(identity(), owner="worker", diagnosis={}, now=100)
        elif operation == "renew":
            orchestrator.renew(identity(), owner="worker", now=150)
        else:
            orchestrator.finish(identity(), owner="worker", resolved=False,
                                evidence={"reason": "explicit rejection"}, now=200)


@pytest.mark.parametrize("evidence", [
    {"checks": "green"},
    {"deployment": {"sha": "b" * 40, "environment": "production",
                    "status": "success", "provider": "koyeb", "deployment_id": "deploy-1"}},
    {"deployment": {"sha": "a" * 40, "environment": "production",
                    "status": "pending", "provider": "koyeb", "deployment_id": "deploy-1"}},
])
def test_resolved_incident_requires_verified_production_deployment(evidence):
    coordinator, r2 = Mock(), Mock()
    with pytest.raises(ValueError):
        RecoveryOrchestrator(coordinator, r2).finish(
            identity(), owner="worker", resolved=True, evidence=evidence, now=200)
    coordinator.finish.assert_not_called()
    r2.put_object_if_absent.assert_not_called()
