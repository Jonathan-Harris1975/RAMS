from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from repo_mgmt import report_writer
from repo_mgmt.r2_client import R2Client, R2Error
from repo_mgmt.repo_bootstrap import BootstrapTarget, _clone_or_refresh, _is_placeholder_secret


def _legacy_report() -> report_writer.RunReport:
    return report_writer.RunReport(
        run_id="2026-08-22T18-00-00Z",
        pipeline="on-brand",
        dry_run=False,
        started_at="2026-08-22T18:00:00Z",
        finished_at="2026-08-22T18:01:00Z",
        issues_total=1,
        issues_applied=1,
        issues_reverted=0,
        issues_skipped=0,
        issues_future_guidance=0,
        issues_manual_review=0,
        tasks=[
            report_writer.TaskReport(
                task_id="task-1",
                classification="code",
                status="applied",
                affected_paths=["src/app.py"],
                patch_plan_ops=1,
                validation_passed=True,
                commit_sha="abc123",
            )
        ],
        validation_commands=["pytest -q"],
        branch="rams/on-brand",
    )


def test_legacy_report_writer_uploads_camel_case_json(settings, mock_r2) -> None:
    report = _legacy_report()
    key = report_writer.write(report, "on-brand", settings, mock_r2, dry_run=False)

    assert key.endswith("/on-brand/2026-08-22T18-00-00Z/report.json")
    kwargs = mock_r2.put_object.call_args.kwargs
    assert kwargs["bucket"] == settings.r2_bucket_audits
    assert kwargs["content_type"] == "application/json"
    payload = json.loads(kwargs["body"].decode("utf-8"))
    assert payload["runId"] == report.run_id
    assert payload["tasks"][0]["validationPassed"] is True
    assert payload["tasks"][0]["affectedPaths"] == ["src/app.py"]


def test_legacy_report_writer_dry_run_never_writes(settings, mock_r2) -> None:
    key = report_writer.write(_legacy_report(), "on-brand", settings, mock_r2, dry_run=True)
    assert key.endswith("/report.json")
    mock_r2.put_object.assert_not_called()


def test_koyeb_secret_reference_with_whitespace_is_recognised() -> None:
    assert _is_placeholder_secret("{{ secret.GITHUB_TOKEN }}") is True
    assert _is_placeholder_secret("{{secret.GITHUB_TOKEN}}") is True
    assert _is_placeholder_secret("resolved-token") is False


def test_bootstrap_fails_closed_on_unresolved_koyeb_repo_url(tmp_path: Path) -> None:
    target = BootstrapTarget(
        label="aims",
        url="{{ secret.RMS_AIMS_REPO_URL }}",
        branch="main",
        path=tmp_path / "aims",
    )
    result = _clone_or_refresh(
        target,
        "resolved-token",
        timeout_seconds=2,
        clone_depth=1,
        max_output_bytes=2048,
    )
    assert result.attempted is False
    assert result.ready is False
    assert result.action == "failed"
    assert "unresolved Koyeb secret reference" in (result.error or "")


def test_r2_object_exists_returns_false_for_missing_key() -> None:
    client = object.__new__(R2Client)
    client._bucket_audits = "audits"
    client._client = MagicMock()
    client._client.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
    )
    assert client.object_exists("audits", "missing.json") is False


def test_r2_object_exists_wraps_transport_failure() -> None:
    client = object.__new__(R2Client)
    client._bucket_audits = "audits"
    client._client = MagicMock()
    client._client.head_object.side_effect = EndpointConnectionError(endpoint_url="https://r2.test")
    with pytest.raises(R2Error, match="object_exists failed"):
        client.object_exists("audits", "object.json")
