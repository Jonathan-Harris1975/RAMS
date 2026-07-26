import json
from unittest.mock import AsyncMock, patch
from repo_mgmt import pipeline as pipeline_mod
from repo_mgmt.report_publisher import RunReport, ValidationSummary


class TestPipelineRun:
    def _run(self, pid, settings, mock_r2, mock_router, audit):
        mock_r2.get_object.return_value = json.dumps(audit).encode()
        with patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router):
            return pipeline_mod.run(pid, settings, mock_r2, dry_run=True)

    def test_empty_audit_produces_zero_committed(
        self, settings, mock_r2, mock_router, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        report = self._run("on-brand", settings, mock_r2, mock_router, {})
        assert report.summary["committed"] == 0 and report.error is None

    def test_code_fix_issue_planned_in_dry_run(
        self, settings, mock_r2, mock_router, sample_audit, tmp_repo, monkeypatch
    ):
        monkeypatch.chdir(tmp_repo.parent)
        settings.rms_website_repo_path = str(tmp_repo)
        report = self._run("on-brand", settings, mock_r2, mock_router, sample_audit)
        assert (
            report.error is None
            and report.pipeline == "on-brand"
            and report.dryRun is True
            and report.summary["codeFixesAttempted"] == 1
            and report.tasks[0]["status"] == "planned"
        )

    def test_returns_report_on_r2_failure(
        self, settings, mock_r2, mock_router, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        mock_r2.get_object.side_effect = Exception("R2 down")
        with patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router):
            report = pipeline_mod.run("on-brand", settings, mock_r2, dry_run=True)
        assert isinstance(report, RunReport) and report.summary["tasksGenerated"] == 0

    def test_concurrent_run_returns_error_report(self, settings, mock_r2, mock_router):
        lock = pipeline_mod._pipeline_locks["mobile-ux"]
        lock.acquire()
        try:
            report = pipeline_mod.run("mobile-ux", settings, mock_r2, dry_run=True)
            assert "already running" in (report.error or "")
        finally:
            lock.release()

    def test_is_running_returns_false_when_idle(self):
        assert pipeline_mod.is_running("on-brand") is False

    def test_global_lock_blocks_a_different_pipeline(
        self, settings, mock_r2, mock_router
    ):
        pipeline_mod._global_pipeline_lock.acquire()
        try:
            report = pipeline_mod.run("on-brand", settings, mock_r2, dry_run=True)
            assert "another RAMS pipeline" in (report.error or "")
        finally:
            pipeline_mod._global_pipeline_lock.release()

    def test_max_issues_per_run_respected(
        self, settings, mock_r2, mock_router, tmp_repo, monkeypatch
    ):
        monkeypatch.chdir(tmp_repo.parent)
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
        with patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router):
            report = pipeline_mod.run("on-brand", settings, mock_r2, dry_run=True)
        assert (
            len([t for t in report.tasks if t.get("classification") == "code_fix"]) <= 1
        )

    def test_report_publisher_is_used_by_pipeline(
        self, settings, mock_r2, mock_router, sample_audit, tmp_repo
    ):
        settings.rms_website_repo_path = str(tmp_repo)
        mock_r2.get_object.return_value = json.dumps(sample_audit).encode()
        with (
            patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router),
            patch("repo_mgmt.pipeline.publish", return_value="dest") as pub,
        ):
            report = pipeline_mod.run("on-brand", settings, mock_r2, dry_run=True)
        pub.assert_called_once()
        assert report.summary["codeFixesAttempted"] == 1

    def test_pipeline_fails_closed_if_git_setup_fails_in_live_mode(
        self, settings, mock_r2, mock_router, sample_audit, tmp_repo
    ):
        settings.rms_website_repo_path = str(tmp_repo)
        mock_r2.get_object.return_value = json.dumps(sample_audit).encode()
        with (
            patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router),
            patch("repo_mgmt.pipeline.GitManager") as gm,
            patch(
                "repo_mgmt.update_executor.run_task", new_callable=AsyncMock
            ) as run_task,
        ):
            gm.return_value.create_branch.side_effect = RuntimeError("no branch")
            report = pipeline_mod.run("on-brand", settings, mock_r2, dry_run=False)
        run_task.assert_not_called()
        assert report.tasks[0][
            "status"
        ] == "manual_review" and "Git/live preflight failed" in (report.error or "")

    def test_live_baseline_failure_blocks_code_fix_execution(
        self, settings, mock_r2, mock_router, sample_audit
    ):
        mock_r2.get_object.return_value = json.dumps(sample_audit).encode()
        baseline = ValidationSummary(
            commands=["python3 scripts/inject_partials.py --validate"],
            passed=False,
            output_tail="DRIFT DETECTED before patch",
        )
        with (
            patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router),
            patch("repo_mgmt.pipeline._preflight_live_repo", return_value=None),
            patch("repo_mgmt.pipeline._run_baseline_validation", return_value=baseline),
            patch(
                "repo_mgmt.update_executor.run_task", new_callable=AsyncMock
            ) as run_task,
        ):
            report = pipeline_mod.run("on-brand", settings, mock_r2, dry_run=False)

        run_task.assert_not_called()
        assert report.baseline_validation is baseline
        assert report.tasks[0]["status"] == "manual_review"
        assert "baseline validation failed before patch" in report.tasks[0]["error"]

    def test_validation_runner_is_active_pipeline_validation_path(self):
        assert hasattr(pipeline_mod.update_executor, "validation_runner")

    def test_run_id_is_used_for_report_branch_and_publish_path(
        self, settings, mock_r2, mock_router, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        mock_r2.get_object.return_value = b"{}"
        fixed = "2026-05-05T03-00-00Z"
        with (
            patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router),
            patch("repo_mgmt.pipeline.publish", return_value="dest") as pub,
        ):
            report = pipeline_mod.run(
                "mobile-ux", settings, mock_r2, dry_run=True, run_id=fixed
            )
        published = pub.call_args.args[0]
        assert report.runId == fixed and published.runId == fixed
        assert report.branch.endswith(f"/mobile-ux/{fixed}")

    def test_publish_failure_writes_local_fallback(
        self, settings, mock_r2, mock_router, sample_audit, tmp_repo, tmp_path
    ):
        settings.rms_website_repo_path = str(tmp_repo)
        settings.rms_report_dir = str(tmp_path)
        mock_r2.get_object.return_value = json.dumps(sample_audit).encode()
        with (
            patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router),
            patch(
                "repo_mgmt.pipeline.publish",
                side_effect=RuntimeError("r2 publish down"),
            ),
        ):
            report = pipeline_mod.run("on-brand", settings, mock_r2, dry_run=True)
        assert "report publish failed" in (report.error or "")
        assert report.publish_status.fallback_path is not None
        assert "fallback-on-brand" in report.publish_status.fallback_path


class TestOptimisationCycleWiring:
    """Covers the RAMS optimisation subsystem call site added to `_run_async`."""

    def test_disabled_by_default_never_touches_r2_listing(
        self, settings, mock_r2, mock_router, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        assert settings.rms_optimisation_enabled is False
        mock_r2.get_object.return_value = b"{}"
        with patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router):
            report = pipeline_mod.run("on-brand", settings, mock_r2, dry_run=True)
        assert report.error is None
        mock_r2.list_objects.assert_not_called()

    def test_only_runs_for_on_brand_pipeline(
        self, settings, mock_r2, mock_router, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        settings.rms_optimisation_enabled = True
        mock_r2.get_object.return_value = b"{}"
        mock_r2.list_objects.return_value = []
        with patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router):
            report = pipeline_mod.run("mobile-ux", settings, mock_r2, dry_run=True)
        assert report.error is None
        mock_r2.list_objects.assert_not_called()

    def test_enabled_ingests_qa_events_without_breaking_the_run(
        self, settings, mock_r2, mock_router, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        settings.rms_optimisation_enabled = True
        mock_r2.get_object.return_value = b"{}"
        mock_r2.list_objects.return_value = ["qa-events/2026-01-01/a.json"]
        mock_r2.get_object_limited.return_value = json.dumps(
            {
                "id": "qa-1",
                "ts": "2026-01-01T00:00:00.000Z",
                "source": "scheduler.dedupe",
                "type": "duplicate_blocked",
                "severity": "low",
                "message": "duplicate blocked",
            }
        ).encode()
        with patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router):
            report = pipeline_mod.run("on-brand", settings, mock_r2, dry_run=True)
        assert report.error is None
        assert mock_r2.list_objects.called

    def test_qa_event_read_failure_never_fails_the_pipeline_run(
        self, settings, mock_r2, mock_router, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        settings.rms_optimisation_enabled = True
        mock_r2.get_object.return_value = b"{}"
        mock_r2.list_objects.side_effect = RuntimeError("bucket unavailable")
        with patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router):
            report = pipeline_mod.run("on-brand", settings, mock_r2, dry_run=True)
        assert report.error is None


def test_automatic_pr_is_created_for_pushed_commits(settings, monkeypatch) -> None:
    settings.rms_create_pr = True
    settings.rms_push_enabled = True
    settings.rms_github_token = "token"
    settings.rms_website_repo_url = "https://github.com/example/site.git"
    settings.rms_website_repo_branch = "main"
    tasks = [
        {
            "taskId": "website-1",
            "title": "Fix canonical",
            "severity": "high",
            "commit_sha": "abc123",
            "pushed": True,
        }
    ]
    fake = type(
        "PR",
        (),
        {
            "number": 31,
            "url": "https://github.com/example/site/pull/31",
            "title": "RAMS website remediation",
            "base": "main",
            "head": "rms-qa/website/run-1",
            "created": True,
        },
    )()
    monkeypatch.setattr(pipeline_mod.github_pr, "create_or_get_pull_request", lambda **kwargs: fake)

    result = pipeline_mod._create_automatic_pr(  # noqa: SLF001
        pipeline_id="website",
        run_id="run-1",
        branch="rms-qa/website/run-1",
        audit_json_key="audits/website/2026-07/run-1/website-audit.json",
        tasks=tasks,
        cfg=settings,
    )

    assert result is not None and result.number == 31
    assert tasks[0]["pullRequestUrl"].endswith("/pull/31")


def test_automatic_pr_is_not_created_without_a_pushed_commit(settings, monkeypatch) -> None:
    settings.rms_create_pr = True
    settings.rms_push_enabled = True
    called = False

    def fail_if_called(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("GitHub should not be called")

    monkeypatch.setattr(pipeline_mod.github_pr, "create_or_get_pull_request", fail_if_called)
    result = pipeline_mod._create_automatic_pr(  # noqa: SLF001
        pipeline_id="website",
        run_id="run-1",
        branch="rms-qa/website/run-1",
        audit_json_key="audits/website/2026-07/run-1/website-audit.json",
        tasks=[{"commit_sha": "abc", "pushed": False}],
        cfg=settings,
    )
    assert result is None
    assert called is False
