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


def test_changed_findings_do_not_reset_incident_retry_budget():
    first = repair_identity("owner/RAMS", 435, "a" * 40, "check", ["lint failed"])
    changed = repair_identity("owner/RAMS", 435, "a" * 40, "check", ["tests failed"])
    assert first.incident_key == changed.incident_key
    assert first.attempt_key == changed.attempt_key
    different_pr = repair_identity("owner/RAMS", 436, "a" * 40, "check", ["lint failed"])
    assert first.incident_key != different_pr.incident_key


@pytest.mark.parametrize("finding", ["bad\\x00payload", "bad\\x1bescape", "bad\\rreturn"])
def test_control_character_findings_are_rejected(finding):
    orchestrator = Mock()
    with pytest.raises(ValueError, match="empty canonical finding"):
        reserve_repair(orchestrator, repository="owner/RAMS", pr_number=435,
                       head_sha="a" * 40, kind="check", findings=[finding],
                       owner="worker-1")
    orchestrator.claim.assert_not_called()


def test_multiline_findings_remain_accepted():
    identity = repair_identity("owner/RAMS", 435, "a" * 40, "check",
                               ["failed test:\\n\\tassert expected"])
    assert identity.head_sha == "a" * 40
