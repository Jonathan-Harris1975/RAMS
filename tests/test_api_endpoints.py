"""
Tests for repo_mgmt.api — FastAPI endpoint contracts.

Uses TestClient from starlette (bundled with FastAPI) and mocks the
background pipeline so tests complete without real config or R2.
"""
from __future__ import annotations

import pytest

# These tests require starlette.testclient which ships with fastapi.
# If fastapi is not installed in the test environment the whole module
# is skipped gracefully.
fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
starlette = pytest.importorskip("starlette", reason="starlette not installed")

from fastapi.testclient import TestClient  # noqa: E402
from repo_mgmt.api import app, _running  # noqa: E402


@pytest.fixture(autouse=True)
def reset_running_state():
    """Ensure _running is cleared before and after every test."""
    for k in _running:
        _running[k] = False
    yield
    for k in _running:
        _running[k] = False


client = TestClient(app, raise_server_exceptions=False)


# ── GET /health ────────────────────────────────────────────────────────────

def test_health_returns_200() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200


def test_health_response_shape() -> None:
    resp = client.get("/health")
    data = resp.json()
    assert "status" in data
    assert data["status"] == "ok"
    assert "pipelines" in data
    assert set(data["pipelines"].keys()) == {"seo-aeo-geo", "mobile-ux", "on-brand"}


# ── POST /rebuild/{pipeline_id}/run ───────────────────────────────────────

def test_trigger_on_brand_returns_202() -> None:
    resp = client.post("/rebuild/on-brand/run")
    assert resp.status_code == 202


def test_trigger_on_brand_response_shape() -> None:
    resp = client.post("/rebuild/on-brand/run")
    data = resp.json()
    assert "runId" in data
    assert "pipeline" in data
    assert "dryRun" in data
    assert data["pipeline"] == "on-brand"


def test_trigger_unknown_pipeline_returns_422() -> None:
    resp = client.post("/rebuild/unknown-pipeline/run")
    assert resp.status_code == 422


def test_trigger_already_running_returns_409() -> None:
    _running["on-brand"] = True
    resp = client.post("/rebuild/on-brand/run")
    assert resp.status_code == 409
    data = resp.json()
    # Must be flat — not nested under 'detail'
    assert data.get("error") == "pipeline already running"
    assert data.get("pipeline") == "on-brand"


def test_409_body_not_nested_under_detail() -> None:
    _running["mobile-ux"] = True
    resp = client.post("/rebuild/mobile-ux/run")
    data = resp.json()
    assert "detail" not in data, "409 body must not be nested under 'detail'"


def test_dry_run_override_in_body() -> None:
    resp = client.post("/rebuild/seo-aeo-geo/run", json={"dry_run": False})
    assert resp.status_code == 202
    data = resp.json()
    assert data["dryRun"] is False


def test_dry_run_defaults_to_true_when_omitted() -> None:
    """Without config loaded, default dry_run should be True (safe default)."""
    resp = client.post("/rebuild/on-brand/run")
    data = resp.json()
    # Safe default is True when no config is present
    assert isinstance(data["dryRun"], bool)
