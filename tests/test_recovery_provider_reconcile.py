"""Provider reconciliation preserves accepted handoffs unless failure is proven."""
from unittest.mock import Mock
import pytest

from repo_mgmt.recovery_provider_reconcile import reconcile_provider_task

TASK = "task-123"


def invoke(status, *, task_id=TASK):
    coordinator = Mock()
    coordinator.reconcile_failed_handoff.return_value = True
    identity = Mock()
    identity.repository = "owner/RAMS"
    identity.head_sha = "a" * 40
    identity.incident_key = "b" * 64
    identity.attempt_key = "c" * 64
    orchestrator = Mock()
    orchestrator.reconcile_failed_handoff.return_value = True
    result = reconcile_provider_task(
        orchestrator, identity, owner="runner-1", expected_task_id=TASK,
        provider_get=lambda _: {"task_id": task_id, "status": status})
    return result, orchestrator


@pytest.mark.parametrize("status", ["queued", "pending", "running"])
def test_in_flight_task_never_releases_handoff(status):
    result, orchestrator = invoke(status)
    assert result == "pending"
    orchestrator.reconcile_failed_handoff.assert_not_called()


def test_successful_task_does_not_release_handoff():
    result, orchestrator = invoke("completed")
    assert result == "completed"
    orchestrator.reconcile_failed_handoff.assert_not_called()


def test_verified_terminal_failure_reopens():
    result, orchestrator = invoke("failed")
    assert result == "reopened"
    orchestrator.reconcile_failed_handoff.assert_called_once()


def test_wrong_task_identity_fails_closed():
    with pytest.raises(ValueError, match="does not match"):
        invoke("failed", task_id="unrelated")


def test_unknown_task_state_fails_closed():
    with pytest.raises(ValueError, match="not terminal"):
        invoke("unknown")


def test_provider_timeout_does_not_reconcile():
    orchestrator = Mock()
    with pytest.raises(TimeoutError):
        reconcile_provider_task(
            orchestrator, Mock(), owner="runner-1", expected_task_id=TASK,
            provider_get=lambda _: (_ for _ in ()).throw(TimeoutError()))
    orchestrator.reconcile_failed_handoff.assert_not_called()
