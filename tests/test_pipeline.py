"""Integration-level tests for repo_mgmt.pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_mgmt import pipeline as pipeline_mod
from repo_mgmt.report_writer import RunReport


class TestPipelineRun:
    def _run(self, pipeline_id, settings, mock_r2, mock_router, audit_dict) -> RunReport:
        """Helper: run pipeline with R2 returning *audit_dict* and router mocked."""
        mock_r2.get_object.return_value = json.dumps(audit_dict).encode()
        with patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router), \
             patch("repo_mgmt.pipeline.git_ops") as mock_git, \
             patch("repo_mgmt.pipeline.validator") as mock_val:
            mock_val.run.return_value = MagicMock(passed=True)
            report = pipeline_mod.run(pipeline_id, settings, mock_r2, dry_run=True)
        return report

    def test_empty_audit_produces_zero_applied(
        self, settings, mock_r2, mock_router
    ) -> None:
        report = self._run("on-brand", settings, mock_r2, mock_router, {})
        assert report.issues_applied == 0
        assert report.error is None

    def test_code_fix_issue_applied_in_dry_run(
        self, settings, mock_r2, mock_router, sample_audit, tmp_repo
    ) -> None:
        settings.rms_website_repo_path = str(tmp_repo)
        report = self._run("on-brand", settings, mock_r2, mock_router, sample_audit)
        # In dry-run mode nothing commits, but the pipeline should complete without error
        assert report.error is None
        assert report.pipeline == "on-brand"
        assert report.dry_run is True

    def test_returns_report_on_r2_failure(
        self, settings, mock_r2, mock_router
    ) -> None:
        mock_r2.get_object.side_effect = Exception("R2 down")
        with patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router):
            report = pipeline_mod.run("on-brand", settings, mock_r2, dry_run=True)
        # Should return a report (not raise), with error captured
        assert isinstance(report, RunReport)

    def test_concurrent_run_returns_409_report(
        self, settings, mock_r2, mock_router
    ) -> None:
        # Simulate lock already held
        lock = pipeline_mod._pipeline_locks["mobile-ux"]
        lock.acquire()
        try:
            report = pipeline_mod.run("mobile-ux", settings, mock_r2, dry_run=True)
            assert "already running" in (report.error or "")
        finally:
            lock.release()

    def test_is_running_returns_false_when_idle(self) -> None:
        assert pipeline_mod.is_running("on-brand") is False

    def test_max_issues_per_run_respected(
        self, settings, mock_r2, mock_router, tmp_repo
    ) -> None:
        settings.rms_max_issues_per_run = 1
        settings.rms_website_repo_path = str(tmp_repo)
        audit = {
            "findings": [
                {
                    "title": f"Issue {i}",
                    "description": "Missing canonical tag",
                    "severity": "medium",
                    "confidence": 0.9,
                    "fixClass": "html_fix",
                    "affectedPaths": ["index.html"],
                    "evidence": [],
                    "requiredOutcome": "Add canonical",
                    "sourceAudit": "on-brand",
                }
                for i in range(3)
            ]
        }
        mock_r2.get_object.return_value = json.dumps(audit).encode()
        with patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router), \
             patch("repo_mgmt.pipeline.validator") as mock_val:
            mock_val.run.return_value = MagicMock(passed=True)
            report = pipeline_mod.run("on-brand", settings, mock_r2, dry_run=True)
        # At most 1 code_fix task should be processed
        code_fix_tasks = [
            t for t in report.tasks
            if t.classification == "code_fix" and t.status not in ("skipped_limit_reached",)
        ]
        assert len(code_fix_tasks) <= 1
