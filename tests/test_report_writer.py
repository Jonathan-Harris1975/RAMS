"""Tests for repo_mgmt.report_writer."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from repo_mgmt.report_writer import RunReport, TaskReport, make_run_id, write


def _sample_report(run_id: str = "2026-05-05T03-00-00Z") -> RunReport:
    return RunReport(
        run_id=run_id,
        pipeline="on-brand",
        dry_run=True,
        started_at="2026-05-05T03:00:00+00:00",
        finished_at="2026-05-05T03:01:00+00:00",
        issues_total=2,
        issues_applied=1,
        issues_reverted=0,
        issues_skipped=0,
        issues_future_guidance=1,
        issues_manual_review=0,
        tasks=[
            TaskReport(
                task_id="rms-on-brand-2026-05-05-001",
                classification="code_fix",
                status="applied",
                affected_paths=["index.html"],
                patch_plan_ops=1,
                validation_passed=True,
                commit_sha=None,
            )
        ],
        validation_commands=["python3 scripts/inject_partials.py --validate"],
    )


class TestMakeRunId:
    def test_format_is_correct(self) -> None:
        run_id = make_run_id()
        # Format: YYYY-MM-DDTHH-MM-SSZ
        assert len(run_id) == 20
        assert run_id.endswith("Z")
        assert run_id[4] == "-"
        assert run_id[10] == "T"


class TestWrite:
    def test_dry_run_skips_upload(self, settings, mock_r2: MagicMock) -> None:
        report = _sample_report()
        key = write(report, "on-brand", settings, mock_r2, dry_run=True)
        mock_r2.put_object.assert_not_called()
        assert "on-brand" in key
        assert "report.json" in key

    def test_live_mode_uploads_json(self, settings, mock_r2: MagicMock) -> None:
        report = _sample_report()
        key = write(report, "on-brand", settings, mock_r2, dry_run=False)
        mock_r2.put_object.assert_called_once()
        call_kwargs = mock_r2.put_object.call_args.kwargs
        body = call_kwargs["body"]
        parsed = json.loads(body)
        assert parsed["pipeline"] == "on-brand"
        assert parsed["dryRun"] is True
        assert len(parsed["tasks"]) == 1

    def test_key_format(self, settings, mock_r2: MagicMock) -> None:
        report = _sample_report("2026-05-05T03-00-00Z")
        key = write(report, "on-brand", settings, mock_r2, dry_run=True)
        assert key.startswith("qa-suite/reports/on-brand/2026-05-05T03-00-00Z/")
        assert key.endswith("report.json")

    def test_task_camel_case_keys(self, settings, mock_r2: MagicMock) -> None:
        report = _sample_report()
        write(report, "on-brand", settings, mock_r2, dry_run=False)
        body = mock_r2.put_object.call_args.kwargs["body"]
        parsed = json.loads(body)
        task = parsed["tasks"][0]
        assert "taskId" in task
        assert "affectedPaths" in task
        assert "task_id" not in task
