"""Workflow recovery report must not claim success on missing D1 evidence."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/recovery_status_report.py"


def load_script():
    spec = importlib.util.spec_from_file_location("rams_recovery_status_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_writes_bounded_credential_free_summary(tmp_path, monkeypatch):
    script = load_script()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/RAMS")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a" * 32)
    monkeypatch.setenv("RAMS_RECOVERY_D1_DATABASE_ID", "759c49e9-0d72-4967-8a32-1e8b6dd6a1f8")
    monkeypatch.setenv("RAMS_RECOVERY_D1_TOKEN", "private-token")
    report = {"repository": "owner/RAMS", "total_returned": 0,
              "active": 0, "resolved": 0, "retry_attempts": 0,
              "event_totals": {}, "incidents": []}
    with patch.object(script, "D1Coordinator") as coordinator, patch.object(
        script, "recovery_summary", return_value=report
    ) as summary:
        script.main()
    summary.assert_called_once_with(coordinator.return_value, "owner/RAMS", limit=50)
    output = (tmp_path / "rams-autonomous-recovery-report.json").read_text()
    assert json.loads(output) == report
    assert "private-token" not in output


def test_report_fails_closed_on_d1_unavailability(tmp_path, monkeypatch):
    script = load_script()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/RAMS")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a" * 32)
    monkeypatch.setenv("RAMS_RECOVERY_D1_DATABASE_ID", "759c49e9-0d72-4967-8a32-1e8b6dd6a1f8")
    monkeypatch.setenv("RAMS_RECOVERY_D1_TOKEN", "private-token")
    with patch.object(script, "D1Coordinator"), patch.object(
        script, "recovery_summary", side_effect=RuntimeError("D1 unavailable")
    ):
        with pytest.raises(RuntimeError, match="D1 unavailable"):
            script.main()
    assert not (tmp_path / "rams-autonomous-recovery-report.json").exists()
