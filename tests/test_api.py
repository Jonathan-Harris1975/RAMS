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
    api_mod._pipelines.clear()
    for pipeline_id in api_mod._running:
        api_mod._running[pipeline_id] = False
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.delenv("GUNICORN_WORKERS", raising=False)


@pytest.fixture
def repo_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Create fake target repo directories for API admission."""
    seo = tmp_path / "seo"
    website = tmp_path / "website"
    seo.mkdir()
    website.mkdir()
    return seo, website


def make_settings(repo_dirs: tuple[Path, Path], **overrides: str) -> Settings:
    """Build Settings with concrete repo paths and optional env overrides."""
    seo, website = repo_dirs
    env: dict[str, str] = {
        "R2_ENDPOINT": "https://test.r2.cloudflarestorage.com",
        "R2_ACCESS_KEY_ID": "test-key-id",
        "R2_SECRET_ACCESS_KEY": "test-secret",
        "R2_BUCKET_AUDITS": "audits",
        "OPENROUTER_API_KEY": "test-or-key",
        "OPENROUTER_PRIMARY_MODEL": "primary/model",
        "OPENROUTER_SECONDARY_MODEL": "secondary/model",
        "OPENROUTER_TRIAGE_MODEL": "triage/model",
        "RMS_SEO_REPO_PATH": str(seo),
        "RMS_WEBSITE_REPO_PATH": str(website),
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
            validation=None,
            commits=[],
        )


def install_valid_api(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    fake_pipeline: FakePipeline | None = None,
) -> MagicMock:
    """Patch config, R2, and optional pipeline construction for an API test."""
    mock_r2 = MagicMock()
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
    assert data["dependencies"] == {
        "config_loaded": True,
        "r2_ready": True,
        "model_router_ready": True,
        "seo_repo_ready": True,
        "website_repo_ready": True,
        "single_worker_mode": True,
    }


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


def test_invalid_repo_path_returns_503_and_schedules_no_background(
    monkeypatch: pytest.MonkeyPatch, repo_dirs: tuple[Path, Path]
) -> None:
    seo, website = repo_dirs
    website.rmdir()
    settings = make_settings((seo, website))
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
    assert response.json()["error"] == "live write refused"
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
