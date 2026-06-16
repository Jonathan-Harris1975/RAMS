"""Production health and response-hardening contract tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from repo_mgmt import api as api_mod


def test_livez_is_public_and_hardened() -> None:
    with TestClient(api_mod.app) as client:
        response = client.get("/livez", headers={"X-Request-ID": "rams-contract-1"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"] == "rams-contract-1"


def test_readyz_is_protected_when_configuration_is_unavailable(monkeypatch) -> None:
    api_mod._cfg = None
    api_mod._cfg_error = None
    monkeypatch.setattr(
        api_mod, "load_settings", lambda: (_ for _ in ()).throw(RuntimeError("missing"))
    )
    with TestClient(api_mod.app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["error"] == "configuration unavailable"
