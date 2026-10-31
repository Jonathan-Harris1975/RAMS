"""Exact-head recovery dispatch coordination tests."""
from unittest.mock import Mock

import pytest

from repo_mgmt.recovery_dispatch import repair_identity, reserve_repair


def test_repair_identity_is_stable_across_findings_order():
    args = ("owner/RAMS", 435, "a" * 40, "check")
    first = repair_identity(*args, ["failed lint", "failed tests"])
    second = repair_identity(*args, ["failed tests", "failed lint"])
    assert first.incident_key == second.incident_key
    assert first.attempt_key == second.attempt_key
    moved = repair_identity("owner/RAMS", 435, "b" * 40, "check",
                            ["failed lint", "failed tests"])
    assert moved.incident_key == first.incident_key
    assert moved.attempt_key != first.attempt_key


def test_claim_uses_durable_orchestrator_and_preserves_diagnosis():
    orchestrator = Mock()
    orchestrator.claim.return_value = True
    identity, acquired = reserve_repair(
        orchestrator, repository="owner/RAMS", pr_number=435,
        head_sha="a" * 40, kind="check", findings=["CI failure"],
        owner="workflow-123",
    )
    assert acquired is True
    assert identity.repository == "owner/RAMS"
    assert orchestrator.claim.call_args.kwargs["diagnosis"]["source_head_sha"] == "a" * 40


@pytest.mark.parametrize("findings", [[], [""] , ["x" * 3501], ["   "], ["x"] * 13])
def test_invalid_findings_never_claim(findings):
    orchestrator = Mock()
    with pytest.raises(ValueError):
        reserve_repair(orchestrator, repository="owner/RAMS", pr_number=1,
                       head_sha="a" * 40, kind="check", findings=findings,
                       owner="worker-1")
    orchestrator.claim.assert_not_called()
