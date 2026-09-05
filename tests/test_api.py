"""Admission and execution-path tests for repo_mgmt.api."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from repo_mgmt import api as api_mod
from repo_mgmt.config import Settings
from repo_mgmt.report_publisher import RunReport


@pytest.fixture(autouse=True)
def reset_api_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset cached API singletons between tests."""
    api_mod._cfg = None
    api_mod._cfg_error = None
    api_mod._r2 = None
    api_mod._r2_error = None
    api_mod._r2_verified = None
    api_mod._r2_verify_error = None
    api_mod._r2_verified_at = None
    api_mod._r2_last_success_at = None
    api_mod._r2_last_failure_at = None
    api_mod._r2_check_count = 0
    api_mod._r2_monitor_task = None
    api_mod._pipelines.clear()
    api_mod._model_router = None
    api_mod._active_pipeline = None
    api_mod._active_run_id = None
    api_mod._shutting_down = False
    api_mod._idempotency.clear()
    api_mod._runtime_details_cache = None
    for pipeline_id in api_mod._running:
        api_mod._running[pipeline_id] = False
    api_mod._bootstrap_attempted = False
    api_mod._bootstrap_results = []
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.delenv("GUNICORN_WORKERS", raising=False)


@pytest.fixture
def repo_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Create fake target repo directories for API admission."""
    website = tmp_path / "website"
    aims = tmp_path / "aims"
    website.mkdir()
    aims.mkdir()
    return website, aims


def make_settings(repo_dirs: tuple[Path, Path], **overrides: str) -> Settings:
    """Build Settings with concrete repo paths and optional env overrides."""
    website, aims = repo_dirs
    env: dict[str, str] = {
        "R2_ENDPOINT": "https://test.r2.cloudflarestorage.com",
        "R2_ACCESS_KEY_ID": "test-key-id",
        "R2_SECRET_ACCESS_KEY": "test-secret",
        "R2_BUCKET_AUDITS": "audits",
        "OPENROUTER_API_KEY": "test-or-key",
        "OPENROUTER_PRIMARY_MODEL": "primary/model",
        "OPENROUTER_SECONDARY_MODEL": "secondary/model",
        "OPENROUTER_TRIAGE_MODEL": "triage/model",
        "RMS_WEBSITE_REPO_PATH": str(website),
        "RMS_AIMS_REPO_PATH": str(aims),
        "RMS_DRY_RUN": "true",
        "RMS_LIVE_WRITE_ENABLED": "false",
        "RMS_ALLOW_UNAUTHENTICATED_DEV": "true",
        "RMS_ENVIRONMENT": "development",
    }
    env.update(overrides)
    from unittest.mock import patch

    with patch.dict("os.environ", env, clear=True):
        return Settings()


class FakePipeline:
    """Fake pipeline that records the actual background execution path."""

    def __init__(self) -> None:
        self.calls: list[tuple[bool, str]] = []
        self.audit_json_keys: list[str | None] = []

    async def run(
        self, dry_run: bool, run_id: str, audit_json_key: str | None = None
    ) -> RunReport:
        self.calls.append((dry_run, run_id))
        self.audit_json_keys.append(audit_json_key)
        return RunReport(
            runId=run_id,
            pipeline="on-brand",
            targetRepo="/tmp/site",
            branch=f"rms-qa/on-brand/{run_id}",
            dryRun=dry_run,
            summary={
                "snapshotsRead": 0,
                "tasksGenerated": 0,
                "codeFixesAttempted": 0,
                "committed": 0,
                "validationFailed": 0,
                "futureGuidance": 0,
                "manualReview": 0,
            },
            tasks=[],
            validation=api_mod.pipeline_mod.ValidationSummary(
                commands=[], passed=False, output_tail="not_run: validation did not run"
            ),
            commits=[],
        )


def install_valid_api(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    fake_pipeline: FakePipeline | None = None,
) -> MagicMock:
    """Patch config, R2, and optional pipeline construction for an API test."""
    mock_r2 = MagicMock()
    mock_r2.verify_bucket.return_value = True
    mock_r2.object_exists.return_value = False
    monkeypatch.setattr(api_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(api_mod, "R2Client", lambda cfg: mock_r2)
    if fake_pipeline is not None:
        monkeypatch.setattr(api_mod, "_get_pipeline", lambda pipeline_id: fake_pipeline)
    return mock_r2


@pytest.mark.parametrize(
    ("version", "expected"),
    [("v22.22.0", True), ("22.0.0", True), ("v21.7.3", False), ("v23.0.0", False), ("v26.0.0", False)],
)
def test_node_validation_runtime_is_pinned_to_major_22(version: str, expected: bool) -> None:
    assert api_mod._node_major_ok(version) is expected


def install_ready_validation_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate readiness unit tests from GitHub runner binary availability."""
    monkeypatch.setattr(
        api_mod,
        "_validation_runtime_details",
        lambda: {
            "ready": True,
            "python": "Python test",
            "git": "git version test",
            "node": "v22.22.0",
            "npm": "10.0.0",
        },
    )


def test_health_reports_exact_contract(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(repo_dirs)
    install_valid_api(monkeypatch, settings)
    with TestClient(api_mod.app) as client:
        response = client.get("/health")
    data = response.json()
    assert response.status_code == 200
    assert data["status"] == "ok"
    assert data["pipelines"] == {
        "website": "idle",
        "seo-aeo-geo": "idle",
        "mobile-ux": "idle",
        "on-brand": "idle",
        "content": "idle",
    }
    # /health now also reports the service lifecycle state (Online/Starting/Busy/
    # Standby/Offline/Maintenance model); Standby itself is tracked by MAST, since a
    # paused instance cannot self-report, so RAMS only ever observes the other states.
    assert data["lifecycle"]["state"] in {"starting", "online", "busy", "maintenance"}
    assert "since" in data["lifecycle"]


def test_readiness_reports_dependency_readiness(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    """Readiness exposes dependency detail moved out of /health."""
    settings = make_settings(repo_dirs)
    install_valid_api(monkeypatch, settings)
    install_ready_validation_runtime(monkeypatch)
    with TestClient(api_mod.app) as client:
        response = client.get("/readiness")
    data = response.json()
    assert response.status_code == 200
    assert data["status"] == "ready"
    deps = data["dependencies"]
    assert deps["config_loaded"] is True
    assert deps["r2_configured"] is True
    assert deps["r2_verified"] is True
    assert deps["model_router_ready"] is True
    assert deps["website_repo_ready"] is True
    assert deps["aims_repo_ready"] is True
    assert deps["pipeline_repo_paths"]["website"].endswith("website")
    assert deps["pipeline_repo_paths"]["seo-aeo-geo"].endswith("website")
    assert deps["pipeline_repo_paths"]["on-brand"].endswith("aims")
    assert deps["validation_runtime_ready"] is True
    assert deps["single_worker_mode"] is True
    assert deps["runtime"]["node"].startswith("v")


def test_readiness_accepts_configured_on_demand_repo_bootstrap(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    """Idle ephemeral Koyeb worktrees must not make RAMS look degraded."""
    website, aims = repo_dirs
    website.rmdir()
    aims.rmdir()
    settings = make_settings(
        repo_dirs,
        RMS_REPO_BOOTSTRAP_ENABLED="true",
        RMS_WEBSITE_REPO_URL="https://github.com/example/website.git",
        RMS_AIMS_REPO_URL="https://github.com/example/aims.git",
    )
    install_valid_api(monkeypatch, settings)
    install_ready_validation_runtime(monkeypatch)

    with TestClient(api_mod.app) as client:
        response = client.get("/readiness")

    data = response.json()
    assert response.status_code == 200, data
    assert data["status"] == "ready"
    deps = data["dependencies"]
    assert deps["website_repo_ready"] is True
    assert deps["aims_repo_ready"] is True
    assert deps["repo_bootstrap"]["materialized"] == {
        "website": False,
        "aims": False,
    }
    assert deps["repo_bootstrap"]["ready_on_demand"] == {
        "website": True,
        "aims": True,
    }


def test_readiness_requires_config_before_exposing_dependency_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api_mod, "load_settings", lambda: (_ for _ in ()).throw(RuntimeError("missing"))
    )
    with TestClient(api_mod.app) as client:
        response = client.get("/readiness")
    data = response.json()
    assert response.status_code == 503
    assert data["error"] == "configuration unavailable"
    assert data["dependencies"]["config_loaded"] is False


@pytest.mark.parametrize(
    "endpoint,pipeline_id",
    [
        ("/rebuild/seo-aeo-geo/run", "seo-aeo-geo"),
        ("/rebuild/mobile-ux/run", "mobile-ux"),
        ("/rebuild/on-brand/run", "on-brand"),
    ],
)
def test_valid_run_request_calls_real_background_pipeline_path(
    monkeypatch: pytest.MonkeyPatch,
    repo_dirs: tuple[Path, Path],
    endpoint: str,
    pipeline_id: str,
) -> None:
    settings = make_settings(repo_dirs)
    fake_pipeline = FakePipeline()
    install_valid_api(monkeypatch, settings, fake_pipeline)
    with TestClient(api_mod.app) as client:
        response = client.post(endpoint)
    data = response.json()
    assert response.status_code == 202
    assert data["pipeline"] == pipeline_id
    assert data["dryRun"] is True
    assert len(fake_pipeline.calls) == 1
    assert fake_pipeline.calls[0][0] is True
    assert fake_pipeline.calls[0][1] == data["runId"]


def test_website_run_requires_exact_final_json_key(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(repo_dirs)
    fake_pipeline = FakePipeline()
    install_valid_api(monkeypatch, settings, fake_pipeline)
    with TestClient(api_mod.app) as client:
        missing = client.post("/rebuild/website/run")
        invalid = client.post(
            "/rebuild/website/run", json={"audit_json_key": "audits/website/latest.json"}
        )
    assert missing.status_code == 422
    assert invalid.status_code == 422
    assert fake_pipeline.calls == []


def test_website_run_passes_exact_final_json_key_to_pipeline(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(repo_dirs)
    fake_pipeline = FakePipeline()
    install_valid_api(monkeypatch, settings, fake_pipeline)
    key = "audits/website/2026-07/site-audit-123/website-audit.json"
    with TestClient(api_mod.app) as client:
        response = client.post(
            "/rebuild/website/run",
            json={"audit_json_key": key, "audit_session_id": "site-audit-123"},
        )
    assert response.status_code == 202, response.json()
    assert response.json()["auditJsonKey"] == key
    assert len(fake_pipeline.calls) == 1
    assert fake_pipeline.audit_json_keys == [key]


def test_website_run_rejects_session_key_mismatch(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(repo_dirs)
    fake_pipeline = FakePipeline()
    install_valid_api(monkeypatch, settings, fake_pipeline)
    with TestClient(api_mod.app) as client:
        response = client.post(
            "/rebuild/website/run",
            json={
                "audit_json_key": "audits/website/2026-07/right-session/website-audit.json",
                "audit_session_id": "wrong-session",
            },
        )
    assert response.status_code == 422
    assert fake_pipeline.calls == []


def test_already_running_conflict_schedules_no_background(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(repo_dirs)
    fake_pipeline = FakePipeline()
    install_valid_api(monkeypatch, settings, fake_pipeline)
    api_mod._running["on-brand"] = True
    with TestClient(api_mod.app) as client:
        response = client.post("/rebuild/on-brand/run")
    assert response.status_code == 409
    assert fake_pipeline.calls == []


def test_rebuild_endpoint_requires_api_key_when_dev_override_disabled(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(
        repo_dirs, RMS_API_KEY="", RMS_ALLOW_UNAUTHENTICATED_DEV="false"
    )
    fake_pipeline = FakePipeline()
    install_valid_api(monkeypatch, settings, fake_pipeline)
    with TestClient(api_mod.app) as client:
        response = client.post("/rebuild/on-brand/run")
    assert response.status_code == 503
    payload = response.json()
    assert payload["error"] == "RMS_API_KEY is required for rebuild endpoints"
    assert "RMS_ALLOW_UNAUTHENTICATED_DEV=true" in payload["hint"]
    assert fake_pipeline.calls == []


def test_rebuild_endpoint_allows_local_dev_override_without_api_key(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(
        repo_dirs, RMS_API_KEY="", RMS_ALLOW_UNAUTHENTICATED_DEV="true"
    )
    fake_pipeline = FakePipeline()
    install_valid_api(monkeypatch, settings, fake_pipeline)
    with TestClient(api_mod.app) as client:
        response = client.post("/rebuild/on-brand/run")
    assert response.status_code == 202
    assert len(fake_pipeline.calls) == 1



def test_rebuild_endpoint_rejects_dev_override_in_production(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(
        repo_dirs, RMS_API_KEY="", RMS_ALLOW_UNAUTHENTICATED_DEV="true", RMS_ENVIRONMENT="production"
    )
    fake_pipeline = FakePipeline()
    install_valid_api(monkeypatch, settings, fake_pipeline)
    with TestClient(api_mod.app) as client:
        response = client.post("/rebuild/on-brand/run")
    assert response.status_code == 503
    assert fake_pipeline.calls == []

def test_readiness_requires_api_key_when_dev_override_disabled(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(
        repo_dirs, RMS_API_KEY="", RMS_ALLOW_UNAUTHENTICATED_DEV="false"
    )
    install_valid_api(monkeypatch, settings)
    with TestClient(api_mod.app) as client:
        response = client.get("/readiness")
    assert response.status_code == 503
    assert response.json()["error"] == "RMS_API_KEY is required for protected endpoints"


def test_readiness_treats_unresolved_secret_placeholder_as_missing(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(
        repo_dirs,
        RMS_API_KEY="{{secret.RMS_API_KEY}}",
        RMS_ALLOW_UNAUTHENTICATED_DEV="false",
    )
    install_valid_api(monkeypatch, settings)
    with TestClient(api_mod.app) as client:
        response = client.get(
            "/readiness", headers={"Authorization": "Bearer {{secret.RMS_API_KEY}}"}
        )
    assert response.status_code == 503
    assert response.json()["error"] == "RMS_API_KEY is required for protected endpoints"


def test_readiness_accepts_bearer_api_key(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(
        repo_dirs, RMS_API_KEY="unit-rms-key", RMS_ALLOW_UNAUTHENTICATED_DEV="false"
    )
    install_valid_api(monkeypatch, settings)
    with TestClient(api_mod.app) as client:
        response = client.get(
            "/readiness", headers={"Authorization": "Bearer unit-rms-key"}
        )
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_missing_config_returns_503_and_schedules_no_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pipeline = FakePipeline()
    monkeypatch.setattr(
        api_mod,
        "load_settings",
        lambda: (_ for _ in ()).throw(RuntimeError("missing config")),
    )
    monkeypatch.setattr(api_mod, "_get_pipeline", lambda pipeline_id: fake_pipeline)
    with TestClient(api_mod.app) as client:
        response = client.post("/rebuild/on-brand/run")
    assert response.status_code == 503
    assert "configuration" in response.json()["error"]
    assert fake_pipeline.calls == []


def test_missing_r2_returns_503_and_schedules_no_background(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(repo_dirs)
    fake_pipeline = FakePipeline()
    monkeypatch.setattr(api_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(
        api_mod, "R2Client", lambda cfg: (_ for _ in ()).throw(RuntimeError("r2 down"))
    )
    monkeypatch.setattr(api_mod, "_get_pipeline", lambda pipeline_id: fake_pipeline)
    with TestClient(api_mod.app) as client:
        response = client.post("/rebuild/on-brand/run")
    assert response.status_code == 503
    assert "R2" in response.json()["error"]
    assert fake_pipeline.calls == []


def test_unverified_r2_returns_503_and_readiness_degraded(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(repo_dirs)
    fake_pipeline = FakePipeline()
    mock_r2 = install_valid_api(monkeypatch, settings, fake_pipeline)
    mock_r2.verify_bucket.return_value = False
    with TestClient(api_mod.app) as client:
        readiness = client.get("/readiness")
        response = client.post("/rebuild/on-brand/run")
    assert readiness.json()["status"] == "degraded"
    assert readiness.json()["dependencies"]["r2_configured"] is True
    assert readiness.json()["dependencies"]["r2_verified"] is False
    assert response.status_code == 503
    assert "R2" in response.json()["error"]
    assert fake_pipeline.calls == []


def test_invalid_repo_path_returns_503_and_schedules_no_background(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    website, aims = repo_dirs
    aims.rmdir()
    settings = make_settings((website, aims))
    fake_pipeline = FakePipeline()
    install_valid_api(monkeypatch, settings, fake_pipeline)
    with TestClient(api_mod.app) as client:
        response = client.post("/rebuild/on-brand/run")
    assert response.status_code == 503
    assert "repo" in response.json()["error"]
    assert fake_pipeline.calls == []


def test_body_cannot_override_dry_run_without_live_gate(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(
        repo_dirs, RMS_DRY_RUN="true", RMS_LIVE_WRITE_ENABLED="false"
    )
    fake_pipeline = FakePipeline()
    install_valid_api(monkeypatch, settings, fake_pipeline)
    with TestClient(api_mod.app) as client:
        response = client.post("/rebuild/on-brand/run", json={"dry_run": False})
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"] == "live write refused"
    assert payload["requestedDryRun"] is False
    assert payload["effectiveDryRun"] is False
    assert payload["envDryRunValue"] is True
    assert payload["liveWriteValue"] is False
    assert "dryRunEnvValueIsFalse" in payload["failedChecks"]
    assert "liveWriteEnvValueIsTrue" in payload["failedChecks"]
    assert fake_pipeline.calls == []


def test_single_worker_limitation_visible_in_readiness(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(repo_dirs)
    install_valid_api(monkeypatch, settings)
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    with TestClient(api_mod.app) as client:
        response = client.get("/readiness")
    assert response.json()["status"] == "degraded"
    assert response.json()["dependencies"]["single_worker_mode"] is False


def test_ops_excellence_exposes_production_control_evidence(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(
        repo_dirs,
        RMS_DRY_RUN="false",
        RMS_LIVE_WRITE_ENABLED="true",
        RMS_PUSH_ENABLED="false",
        RMS_CREATE_PR="false",
        RMS_RELEASE_ID="unit-release",
    )
    install_valid_api(monkeypatch, settings)

    with TestClient(api_mod.app) as client:
        response = client.get("/ops/excellence")

    assert response.status_code == 200
    payload = response.json()
    assert payload["releaseId"] == "unit-release"
    assert payload["deploymentContract"]["target"] == "paid Koyeb production instance"
    assert payload["deploymentContract"]["healthCheckPath"] == "/health"
    assert payload["deploymentContract"]["maxConcurrentPipelines"] == 1
    assert payload["deploymentContract"]["warmupExternalWork"] is False
    controls = payload["liveWriteControls"]
    assert controls["dryRunDefault"] is False
    assert controls["liveWriteEnabled"] is True
    assert controls["liveWritePermitted"] is True
    assert controls["pushEnabled"] is False
    assert controls["createPr"] is False
    assert "automatically creates or reuses one GitHub pull request" in controls["meaning"]
    assert payload["modelProviderPolicy"] == {
        "promptLogging": False,
        "dataCollection": "deny",
        "fallbacksEnabled": True,
        "maxRetries": 0,
    }
    assert "/rebuild/{pipeline_id}/run" in payload["protectedEndpoints"]


def _write_dry_run_report(report_dir: Path, pipeline: str, run_id: str) -> Path:
    """Write a minimal dry-run report fixture to the configured report directory."""
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"dry-run-{pipeline}-{run_id}-report.json"
    path.write_text(
        '{"runId":"%s","pipeline":"%s","dryRun":true,"summary":{"tasksGenerated":0}}'
        % (run_id, pipeline),
        encoding="utf-8",
    )
    return path


def test_dry_run_report_latest_endpoint_returns_newest_report(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    """Operators can fetch the newest container-local dry-run report over HTTP."""
    report_dir = tmp_path / "reports"
    older = "2026-05-15T12-16-44Z"
    newer = "2026-05-15T12-22-04Z"
    _write_dry_run_report(report_dir, "mobile-ux", older)
    _write_dry_run_report(report_dir, "mobile-ux", newer)
    settings = make_settings(repo_dirs, RMS_REPORT_DIR=str(report_dir))
    install_valid_api(monkeypatch, settings)

    with TestClient(api_mod.app) as client:
        response = client.get("/reports/dry-run/mobile-ux/latest")

    assert response.status_code == 200
    assert response.json()["runId"] == newer
    assert response.json()["pipeline"] == "mobile-ux"


def test_dry_run_report_specific_endpoint_returns_requested_report(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    """Operators can fetch a specific dry-run report by run ID."""
    report_dir = tmp_path / "reports"
    run_id = "2026-05-15T12-21-37Z"
    _write_dry_run_report(report_dir, "on-brand", run_id)
    settings = make_settings(repo_dirs, RMS_REPORT_DIR=str(report_dir))
    install_valid_api(monkeypatch, settings)

    with TestClient(api_mod.app) as client:
        response = client.get(f"/reports/dry-run/on-brand/{run_id}")

    assert response.status_code == 200
    assert response.json()["runId"] == run_id
    assert response.json()["pipeline"] == "on-brand"


def test_dry_run_report_list_endpoint_returns_metadata(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    """The report index exposes report URLs without reading every full payload."""
    report_dir = tmp_path / "reports"
    run_id = "2026-05-15T12-22-04Z"
    _write_dry_run_report(report_dir, "seo-aeo-geo", run_id)
    settings = make_settings(repo_dirs, RMS_REPORT_DIR=str(report_dir))
    install_valid_api(monkeypatch, settings)

    with TestClient(api_mod.app) as client:
        response = client.get("/reports/dry-run/seo-aeo-geo")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["latestRunId"] == run_id
    assert data["reports"][0]["url"] == f"/reports/dry-run/seo-aeo-geo/{run_id}"


def test_missing_dry_run_report_returns_helpful_404(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    """A missing report returns a clear operator-facing 404 rather than route 404."""
    settings = make_settings(repo_dirs, RMS_REPORT_DIR=str(tmp_path / "reports"))
    install_valid_api(monkeypatch, settings)

    with TestClient(api_mod.app) as client:
        response = client.get("/reports/dry-run/mobile-ux/latest")

    assert response.status_code == 404
    assert response.json()["error"] == "dry-run report not found"
    assert "Run the pipeline" in response.json()["hint"]


def test_latest_dry_run_report_returns_pending_while_pipeline_running(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    """A report fetch during an active first run should say pending, not 404."""
    settings = make_settings(repo_dirs, RMS_REPORT_DIR=str(tmp_path / "reports"))
    install_valid_api(monkeypatch, settings)
    api_mod._running["mobile-ux"] = True

    with TestClient(api_mod.app) as client:
        response = client.get("/reports/dry-run/mobile-ux/latest")

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert "Retry" in response.json()["hint"]


def test_specific_dry_run_report_returns_pending_while_pipeline_running(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    """A specific report fetch during an active run should say pending, not 404."""
    settings = make_settings(repo_dirs, RMS_REPORT_DIR=str(tmp_path / "reports"))
    install_valid_api(monkeypatch, settings)
    api_mod._running["seo-aeo-geo"] = True
    run_id = "2026-05-15T16-00-15Z"

    with TestClient(api_mod.app) as client:
        response = client.get(f"/reports/dry-run/seo-aeo-geo/{run_id}")

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert response.json()["runId"] == run_id


def test_live_report_latest_endpoint_reads_r2_latest(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    """Operators can fetch the newest live report published to R2."""
    settings = make_settings(repo_dirs)
    mock_r2 = install_valid_api(monkeypatch, settings)
    payload = {"runId": "2026-05-15T18-37-57Z", "pipeline": "mobile-ux"}
    import json

    mock_r2.get_object.return_value = json.dumps(payload).encode("utf-8")

    with TestClient(api_mod.app) as client:
        response = client.get("/reports/mobile-ux/latest")

    assert response.status_code == 200
    assert response.json() == payload
    mock_r2.get_object.assert_called_once_with(
        settings.r2_bucket_audits,
        f"{settings.rms_report_prefix}/mobile-ux/latest.json",
    )


def test_live_report_specific_endpoint_reads_r2_report(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    """Operators can fetch a specific live report from R2 by run ID."""
    settings = make_settings(repo_dirs)
    mock_r2 = install_valid_api(monkeypatch, settings)
    run_id = "2026-05-15T18-37-57Z"
    payload = {"runId": run_id, "pipeline": "mobile-ux"}
    import json

    mock_r2.get_object.return_value = json.dumps(payload).encode("utf-8")

    with TestClient(api_mod.app) as client:
        response = client.get(f"/reports/mobile-ux/{run_id}")

    assert response.status_code == 200
    assert response.json() == payload
    mock_r2.get_object.assert_called_once_with(
        settings.r2_bucket_audits,
        f"{settings.rms_report_prefix}/mobile-ux/{run_id}/report.json",
    )


def test_latest_live_report_returns_pending_while_pipeline_running(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    """A live report fetch during an active run should say pending, not 404."""
    from repo_mgmt.r2_client import R2Error

    settings = make_settings(repo_dirs)
    mock_r2 = install_valid_api(monkeypatch, settings)
    mock_r2.get_object.side_effect = R2Error("NoSuchKey")
    api_mod._running["mobile-ux"] = True

    with TestClient(api_mod.app) as client:
        response = client.get("/reports/mobile-ux/latest")

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert response.json()["key"].endswith("/mobile-ux/latest.json")


def test_cross_pipeline_run_is_globally_rejected_on_emicro(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(repo_dirs)
    fake_pipeline = FakePipeline()
    install_valid_api(monkeypatch, settings, fake_pipeline)
    api_mod._running["mobile-ux"] = True
    with TestClient(api_mod.app) as client:
        response = client.post("/rebuild/on-brand/run")
    assert response.status_code == 429
    assert response.json()["activePipeline"] == "mobile-ux"
    assert response.headers["Retry-After"] == str(settings.rms_busy_retry_after_seconds)
    assert fake_pipeline.calls == []


def test_idempotency_key_replays_original_run_id(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(repo_dirs)
    fake_pipeline = FakePipeline()
    install_valid_api(monkeypatch, settings, fake_pipeline)
    headers = {"X-Idempotency-Key": "mast-job-2026-06-12"}
    with TestClient(api_mod.app) as client:
        first = client.post("/rebuild/on-brand/run", headers=headers)
        second = client.post("/rebuild/on-brand/run", headers=headers)
    assert first.status_code == second.status_code == 202
    assert second.json()["idempotentReplay"] is True
    assert second.json()["runId"] == first.json()["runId"]
    assert len(fake_pipeline.calls) == 1


def test_ops_warmup_is_local_only(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(repo_dirs)
    install_valid_api(monkeypatch, settings)
    fake_router = MagicMock()
    fake_router.warmup.return_value = {
        "ready": True,
        "maxConnections": 2,
        "maxKeepaliveConnections": 1,
    }
    monkeypatch.setattr(api_mod, "_get_model_router", lambda: fake_router)
    with TestClient(api_mod.app) as client:
        response = client.get("/ops/warmup")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "warm"
    assert "OpenRouter requests" in payload["excludedWork"]
    fake_router.warmup.assert_called_once_with()


def test_operational_excellence_reports_audit_verification(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(repo_dirs, RMS_API_KEY="test-key", RMS_RELEASE_ID="release-123")
    mock_r2 = MagicMock()
    mock_r2.verify_bucket.return_value = True
    monkeypatch.setattr(api_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(api_mod, "R2Client", lambda cfg: mock_r2)
    with TestClient(api_mod.app) as client:
        response = client.get(
            "/ops/excellence", headers={"Authorization": "Bearer test-key"}
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert payload["releaseId"] == "release-123"
    assert payload["auditStorage"]["verified"] is True
    assert payload["auditStorage"]["reportRetentionDays"] == 180


def test_model_governance_apply_is_authenticated_persisted_and_reloads_router(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(repo_dirs, RMS_API_KEY="test-key")
    mock_r2 = install_valid_api(monkeypatch, settings)
    old_router = MagicMock()
    old_router.aclose = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock()
    api_mod._model_router = old_router
    registry = {
        "coding": [
            {"model_id": "openai/gpt-5.2-codex", "score": 0.98},
            {"model_id": "anthropic/claude-sonnet-4.6", "score": 0.90},
        ],
        "reasoning": [{"model_id": "openai/gpt-5.2", "score": 0.97}],
        "fast": [{"model_id": "google/gemini-2.5-flash-lite", "score": 0.95}],
    }
    with TestClient(api_mod.app) as client:
        unauthorised = client.post(
            "/ops/model-governance/apply",
            json={"sourceRunId": "council-1", "registry": registry},
        )
        response = client.post(
            "/ops/model-governance/apply",
            headers={"Authorization": "Bearer test-key"},
            json={"sourceRunId": "council-1", "registry": registry},
        )

    assert unauthorised.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["applied"] is True
    assert payload["persisted"] is True
    assert payload["sourceRunId"] == "council-1"
    assert settings.openrouter_primary_model == "openai/gpt-5.2-codex"
    assert settings.openrouter_secondary_model == "anthropic/claude-sonnet-4.6"
    assert settings.openrouter_triage_model == "google/gemini-2.5-flash-lite"
    mock_r2.put_object.assert_called_once()
    assert mock_r2.put_object.call_args.args[1] == "state/model-governance/rams.json"
    old_router.aclose.assert_awaited_once_with()
    assert api_mod._model_router is None
