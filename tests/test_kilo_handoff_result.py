from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github/scripts/kilo_handoff_result.py"
SPEC = importlib.util.spec_from_file_location("kilo_handoff_result", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_kilo_403_is_persistable_and_escalates_owner_action_without_secret() -> None:
    result = MODULE.classify_status("403")

    assert result["state"] == "rejected-http-403"
    assert "Owner action required" in result["guidance"]
    assert "KILO_REPAIR_TRIGGER_URL" in result["guidance"]
    assert "Do not expose or copy the secret URL" in result["guidance"]


def test_kilo_transport_and_server_failures_are_retryable_but_bounded() -> None:
    assert MODULE.classify_status("000")["state"] == "retryable-http-000"
    assert MODULE.classify_status("503")["state"] == "retryable-http-503"


def test_kilo_success_and_unexpected_redirect_are_classified_safely() -> None:
    assert MODULE.classify_status("204")["state"] == "accepted"
    assert MODULE.classify_status("302")["state"] == "retryable-http-302"
    assert "owner must inspect" in MODULE.classify_status("302")["guidance"]


def test_repair_workflow_persists_correlated_failure_and_limits_retries() -> None:
    workflow = (ROOT / ".github/workflows/autonomous-repair.yml").read_text()

    assert "dispatch-state: rejected-http-" in workflow
    assert "dispatch-state: retryable-http-" in workflow
    assert '"$failed_attempts" -ge 2' in workflow
    assert '"$KILO_REPAIR_TRIGGER_URL" || true)' in workflow
    assert 'source SHA $HEAD_SHA' in workflow
    assert 'Incident details are retained on carrier PR #$repair_pr' in workflow
