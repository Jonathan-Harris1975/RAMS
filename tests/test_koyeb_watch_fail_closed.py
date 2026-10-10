"""Regression checks for deployment watcher fail-closed semantics."""
from __future__ import annotations

import importlib.util
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
