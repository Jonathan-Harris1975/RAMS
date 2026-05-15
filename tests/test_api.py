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
    api_mod._pipelines.clear()
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
    }
    env.update(overrides)
    from unittest.mock import patch

    with patch.dict("os.environ", env, clear=True):
        return Settings()


class FakePipeline:
    """Fake pipeline that records the actual background execution path."""

    def __init__(self) -> None:
        self.calls: list[tuple[bool, str]] = []

    async def run(self, dry_run: bool, run_id: str) -> RunReport:
        self.calls.append((dry_run, run_id))
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
    monkeypatch.setattr(api_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(api_mod, "R2Client", lambda cfg: mock_r2)
    if fake_pipeline is not None:
        monkeypatch.setattr(api_mod, "_get_pipeline", lambda pipeline_id: fake_pipeline)
    return mock_r2


def test_health_reports_exact_contract(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    settings = make_settings(repo_dirs)
    install_valid_api(monkeypatch, settings)
    with TestClient(api_mod.app) as client:
        response = client.get("/health")
    data = response.json()
    assert response.status_code == 200
    assert data == {
        "status": "ok",
        "pipelines": {
            "seo-aeo-geo": "idle",
            "mobile-ux": "idle",
            "on-brand": "idle",
        },
    }



def test_readiness_reports_dependency_readiness(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    """Readiness exposes dependency detail moved out of /health."""
    settings = make_settings(repo_dirs)
    install_valid_api(monkeypatch, settings)
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
    assert deps["pipeline_repo_paths"]["seo-aeo-geo"].endswith("website")
    assert deps["pipeline_repo_paths"]["on-brand"].endswith("aims")
    assert deps["validation_runtime_ready"] is True
    assert deps["single_worker_mode"] is True
    assert deps["runtime"]["node"].startswith("v")


def test_readiness_degraded_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        api_mod, "load_settings", lambda: (_ for _ in ()).throw(RuntimeError("missing"))
    )
    with TestClient(api_mod.app) as client:
        response = client.get("/readiness")
    data = response.json()
    assert response.status_code == 200
    assert data["status"] == "degraded"
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
    settings = make_settings(repo_dirs, RMS_DRY_RUN="true", RMS_LIVE_WRITE_ENABLED="false")
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
