"""Regression checks for deployment watcher fail-closed semantics."""
from __future__ import annotations

import importlib.util
from contextlib import redirect_stderr
from io import StringIO
import os
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/watch_koyeb_deployment.py"


def _load():
    import sys
    sys.path.insert(0, str(MODULE_PATH.parent))
    spec = importlib.util.spec_from_file_location("rams_watch_fail_closed", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_provider_configuration_fails_closed():
    watcher = _load()
    with patch.dict(os.environ, {"KOYEB_TOKEN": "", "KOYEB_SERVICE": ""}):
        assert watcher.main() == 1


def test_missing_expected_commit_fails_closed():
    watcher = _load()
    with patch.dict(os.environ, {"KOYEB_TOKEN": "synthetic", "KOYEB_SERVICE": "synthetic", "EXPECTED_DEPLOYMENT_SHA": "", "GITHUB_SHA": ""}):
        assert watcher.main() == 1


def test_invalid_expected_timestamp_fails_closed():
    watcher = _load()
    with patch.dict(os.environ, {"KOYEB_TOKEN": "synthetic", "KOYEB_SERVICE": "synthetic", "EXPECTED_DEPLOYMENT_SHA": "a" * 40, "EXPECTED_DEPLOYMENT_AFTER": "not-a-date"}):
        assert watcher.main() == 1


def test_sleeping_deployment_is_not_healthy():
    watcher = _load()
    assert "sleeping" not in watcher.SUCCESS


def test_old_sha_cannot_match():
    watcher = _load()
    assert not watcher._matches_expected_deployment({"id": "d1", "status": "healthy", "commit_sha": "b" * 40}, "a" * 40, None)


def test_image_digest_requires_immutable_sha256():
    watcher = _load()
    assert watcher._image_digest({"image_digest": "sha256:" + "a" * 64}) == "sha256:" + "a" * 64
    assert watcher._image_digest({"image_digest": "latest"}) == ""
    assert watcher._image_digest({"image_digest": "sha256:" + "a" * 64, "digest": "sha256:" + "b" * 64}) == ""


def test_attestation_requires_observed_identity(tmp_path):
    watcher = _load()
    target = tmp_path / "attestation.json"
    with patch.dict(os.environ, {"DEPLOYMENT_ATTESTATION_PATH": str(target)}):
        assert not watcher._write_attestation({"id": "deployment-1"}, "a" * 40, "service")
        assert not target.exists()
        assert not watcher._write_attestation({"id": "deployment-1", "status": "healthy", "image_digest": "sha256:" + "c" * 64}, "a" * 40, "service")
        assert not target.exists()
        assert not watcher._write_attestation({"id": "deployment-1", "status": "unhealthy", "commit_sha": "a" * 40, "image_digest": "sha256:" + "c" * 64}, "a" * 40, "service")
        assert not target.exists()
        assert not watcher._write_attestation({"id": "deployment-1", "status": "healthy", "commit_sha": "b" * 40, "image_digest": "sha256:" + "c" * 64}, "a" * 40, "service")
        assert not target.exists()
        assert not watcher._write_attestation({"id": "deployment-1", "status": "healthy", "commit_sha": "a" * 40, "service_id": "other-service", "image_digest": "sha256:" + "c" * 64}, "a" * 40, "service")
        assert not target.exists()
        assert watcher._write_attestation({"id": "deployment-1", "status": "healthy", "commit_sha": "a" * 40, "image_digest": "sha256:" + "c" * 64}, "a" * 40, "service")
        import json
        evidence = json.loads(target.read_text())
        assert evidence["deployment_id"] == "deployment-1"
        assert evidence["image_digest"] == "sha256:" + "c" * 64



def test_missing_attestation_path_refuses_with_operational_evidence():
    watcher = _load()
    expected = "a" * 40
    item = {
        "id": "deployment-43",
        "status": "healthy",
        "commit_sha": expected,
        "image_digest": "sha256:" + "c" * 64,
    }
    error_log = StringIO()
    with patch.dict(os.environ, {"DEPLOYMENT_ATTESTATION_PATH": ""}), \
         patch.object(watcher, "send_event", return_value=True) as alert, \
         redirect_stderr(error_log):
        assert not watcher._write_attestation(item, expected, "service-123")
    alert.assert_called_once()
    event = alert.call_args.args[0]
    assert "output path is not configured" in error_log.getvalue()
    assert event["details"]["expectedCommitMatched"] is True
    assert event["details"]["immutableImageDigestPresent"] is True
    assert event["details"]["deploymentIdPresent"] is True
    assert event["details"]["serviceIdentityConflict"] is False


def test_attestation_refusal_sends_redacted_operational_event(tmp_path):
    watcher = _load()
    target = tmp_path / "attestation.json"
    expected = "a" * 40
    item = {
        "id": "deployment-42",
        "status": "healthy",
        "commit_sha": expected,
        "provider_private_payload": "must-not-be-forwarded",
    }
    error_log = StringIO()
    with patch.dict(os.environ, {
        "DEPLOYMENT_ATTESTATION_PATH": str(target),
        "GITHUB_REPOSITORY": "Jonathan-Harris1975/RAMS",
        "GITHUB_RUN_ID": "12345",
        "SERVICE_DISPLAY_NAME": "RAMS",
    }), patch.object(watcher, "send_event", return_value=False) as alert, redirect_stderr(error_log):
        assert not watcher._write_attestation(item, expected, "service-123")
    alert.assert_called_once()
    event = alert.call_args.args[0]
    assert "Missing immutable image digest or deployment ID." in error_log.getvalue()
    assert "could not be delivered" in error_log.getvalue()
    assert "must-not-be-forwarded" not in error_log.getvalue()
    assert event["event_type"] == "deployment_attestation_refused"
    assert event["release_id"] == expected
    assert event["details"]["deploymentId"] == "deployment-42"
    assert event["details"]["providerStatus"] == "healthy"
    assert event["details"]["expectedCommitMatched"] is True
    assert event["details"]["immutableImageDigestPresent"] is False
    assert event["details"]["deploymentIdPresent"] is True
    assert event["details"]["serviceIdentityConflict"] is False
    assert "must-not-be-forwarded" not in str(event)
    assert not target.exists()
