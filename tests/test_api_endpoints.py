"""Basic FastAPI route-shape tests for repo_mgmt.api."""

from __future__ import annotations

from fastapi.testclient import TestClient

from repo_mgmt import api as api_mod
from repo_mgmt.api import app


def test_unknown_pipeline_returns_422() -> None:
    with TestClient(app) as client:
        response = client.post("/rebuild/unknown-pipeline/run")
    assert response.status_code == 422


def test_root_returns_public_health_payload() -> None:
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "pipelines" in response.json()
    assert "dependencies" not in response.json()


def test_readiness_route_is_not_public_without_configured_auth(monkeypatch) -> None:
    api_mod._cfg = None
    api_mod._cfg_error = None
    monkeypatch.setattr(
        api_mod, "load_settings", lambda: (_ for _ in ()).throw(RuntimeError("missing"))
    )
    with TestClient(app) as client:
        response = client.get("/readiness")
    assert response.status_code == 503
    assert response.json()["error"] == "configuration unavailable"
