"""Tests for repo_mgmt.api (FastAPI endpoints)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from repo_mgmt.api import app, _running
from repo_mgmt.report_publisher import RunReport
from tests.conftest import VALID_ENV


def _mock_report(pipeline_id: str = "on-brand") -> RunReport:
    return RunReport(
        runId="2026-05-05T03-00-00Z",
        pipeline=pipeline_id,
        targetRepo="/tmp/repo",
        branch="",
        dryRun=True,
        summary={"snapshotsRead":0,"tasksGenerated":0,"codeFixesAttempted":0,"committed":0,"validationFailed":0,"futureGuidance":0,"manualReview":0},
        tasks=[],
        validation=None,
        commits=[],
    )

@pytest.fixture
def client(settings) -> TestClient:
    """TestClient with all external dependencies mocked."""
    mock_r2 = MagicMock()
    with patch("repo_mgmt.api.load_settings", return_value=settings), \
         patch("repo_mgmt.api.R2Client", return_value=mock_r2), \
         patch("repo_mgmt.api.build_scheduler") as mock_sched, \
         patch("repo_mgmt.api.pipeline_mod.run", return_value=_mock_report()):
        mock_sched.return_value.start = MagicMock()
        with TestClient(app) as c:
            yield c


@pytest.fixture(autouse=True)
def reset_running(client) -> None:
    """Ensure _running is cleared before and after every test."""
    for k in _running:
        _running[k] = False
    yield
    for k in _running:
        _running[k] = False


class TestHealthEndpoint:
    def test_health_returns_200(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "pipelines" in data
        for pid in ("seo-aeo-geo", "mobile-ux", "on-brand"):
            assert pid in data["pipelines"]


class TestRunEndpoints:
    @pytest.mark.parametrize("endpoint,pipeline_id", [
        ("/rebuild/seo-aeo-geo/run", "seo-aeo-geo"),
        ("/rebuild/mobile-ux/run", "mobile-ux"),
        ("/rebuild/on-brand/run", "on-brand"),
    ])
    def test_post_returns_202(
        self, client: TestClient, endpoint: str, pipeline_id: str
    ) -> None:
        response = client.post(endpoint)
        assert response.status_code == 202
        data = response.json()
        assert "runId" in data
        assert data["pipeline"] == pipeline_id
        assert "dryRun" in data

    def test_dry_run_override_in_body(self, client: TestClient) -> None:
        response = client.post(
            "/rebuild/on-brand/run",
            json={"dry_run": False},
        )
        assert response.status_code == 202

    def test_409_when_pipeline_running(self, client: TestClient) -> None:
        """409 is returned when _running flag is already True for the pipeline."""
        # Set _running directly — same pattern as test_api_endpoints.py
        _running["on-brand"] = True
        try:
            response = client.post("/rebuild/on-brand/run")
            assert response.status_code == 409
            data = response.json()
            # Response must be flat — NOT nested under 'detail'
            assert "already running" in data["error"]
            assert data.get("pipeline") == "on-brand"
            assert "detail" not in data
        finally:
            _running["on-brand"] = False

    def test_invalid_body_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/rebuild/on-brand/run",
            json={"dry_run": "not-a-boolean"},
        )
        assert response.status_code == 422
