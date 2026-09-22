from __future__ import annotations

import re
from pathlib import Path


WORKFLOW_PATH = Path(__file__).parents[1] / ".github/workflows/koyeb-deployment-watch.yml"


def _workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _step(text: str, name: str) -> str:
    match = re.search(
        rf"      - name: {re.escape(name)}\n(?P<body>[\s\S]*?)(?=\n      - (?:name:|uses:|if:)|\n\n  [a-zA-Z_]|\Z)",
        text,
    )
    assert match is not None, f"workflow step not found: {name}"
    return match.group("body")


def test_automatic_production_watcher_fails_closed_without_koyeb_configuration() -> None:
    config = _step(_workflow(), "Check Koyeb deployment-watch configuration")

    assert "for name in KOYEB_TOKEN KOYEB_SERVICE" in config
    assert "exit 1" in config
    assert "::error::RAMS production deployment verification cannot run" in config
    assert "::warning::" not in config
    assert "configured=false" not in config
    assert "skipped" not in config.lower()


def test_exact_sha_watch_gates_attestation_and_retained_evidence() -> None:
    text = _workflow()
    watch = text.index("- name: Watch production deployment")
    attestation = text.index("- name: Record exact-SHA production deployment attestation")
    evidence = text.index("- name: Retain production deployment evidence")
    dispatch = text.index("- name: Trigger central ecosystem smoke")

    assert watch < attestation < evidence < dispatch
    assert "EXPECTED_DEPLOYMENT_SHA: ${{ github.event.workflow_run.head_sha || github.sha }}" in _step(
        text, "Watch production deployment"
    )
    assert "DEPLOYED_SHA: ${{ github.event.workflow_run.head_sha || github.sha }}" in _step(
        text, "Record exact-SHA production deployment attestation"
    )
    for name in (
        "Watch production deployment",
        "Record exact-SHA production deployment attestation",
        "Retain production deployment evidence",
    ):
        body = _step(text, name)
        assert "continue-on-error" not in body
        assert "deployment_config.outputs.configured" not in body


def test_smoke_dispatch_is_not_a_substitute_for_attestation() -> None:
    text = _workflow()
    evidence = _step(text, "Retain production deployment evidence")
    dispatch = _step(text, "Trigger central ecosystem smoke")

    assert "deployment-attestation.json" in evidence
    assert "ECOSYSTEM_SMOKE_DISPATCH_TOKEN is required" in dispatch
    assert text.index("Retain production deployment evidence") < text.index(
        "Trigger central ecosystem smoke"
    )


def test_alert_delivery_cannot_turn_verification_steps_non_blocking() -> None:
    text = _workflow()
    config = _step(text, "Check Koyeb deployment-watch configuration")
    watch = _step(text, "Watch production deployment")

    assert "continue-on-error" not in config
    assert "continue-on-error" not in watch
    assert "OPS_ALERT_WEBHOOK_URL" in watch
    assert "OPS_ALERT_WEBHOOK_TOKEN" in watch


def test_workflow_actions_are_immutably_pinned() -> None:
    uses = re.findall(r"^\s*- uses:\s*([^\s#]+)", _workflow(), flags=re.MULTILINE)
    assert uses
    for action in uses:
        assert re.search(r"@[0-9a-f]{40}$", action), action
