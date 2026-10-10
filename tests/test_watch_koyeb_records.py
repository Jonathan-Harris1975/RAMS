"""Regression coverage for deployment records versus nested Koyeb build steps."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("deployment_watch", SCRIPTS / "watch_koyeb_deployment.py")
assert spec and spec.loader
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class DeploymentRecords(unittest.TestCase):
    def record(self, status="healthy"):
        return {"id": "deployment-1", "status": status, "created_at": "2026-10-04T10:00:00Z",
                "definition": {"git": {"sha": "a" * 40}},
                "image_digest": "sha256:" + "c" * 64,
                "steps": [{"name": "build", "status": "completed", "created_at": "2026-10-04T10:01:00Z"}]}

    def test_nested_completed_build_is_never_a_deployment(self):
        item = self.record()
        for payload in ([item], {"deployments": [item]}):
            result = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
            with patch.object(watch.subprocess, "run", return_value=result):
                self.assertEqual(watch._deployments("service", "test-token"), [item])

    def test_malformed_collection_fails_closed(self):
        for payload in ({"build": self.record()}, {"deployments": {}}, [None], [{"status": "healthy"}]):
            with self.assertRaises(ValueError):
                list(watch._walk(payload))

    def test_parent_deployment_status_controls_result(self):
        for status, expected in (("healthy", 0), ("unhealthy", 1), ("provisioning", 1)):
            result = subprocess.CompletedProcess([], 0, json.dumps({"deployments": [self.record(status)]}), "")
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"KOYEB_SERVICE": "service", "KOYEB_TOKEN": "test-token",
                                         "EXPECTED_DEPLOYMENT_SHA": "a" * 40, "EXPECTED_DEPLOYMENT_AFTER": "",
                                         "KOYEB_DEPLOYMENT_MAX_ATTEMPTS": "1",
                                         "DEPLOYMENT_ATTESTATION_PATH": str(Path(directory) / "attestation.json")}), \
                    patch.object(watch.subprocess, "run", return_value=result), \
                    patch.object(watch.time, "sleep"), patch.object(watch, "send_event"):
                self.assertEqual(watch.main(), expected)
                target = Path(directory) / "attestation.json"
                self.assertEqual(target.exists(), expected == 0)
                if expected == 0:
                    self.assertEqual(json.loads(target.read_text())["image_digest"], "sha256:" + "c" * 64)

    def test_wrong_commit_remains_ineligible(self):
        self.assertFalse(watch._matches_expected_deployment(self.record(), "b" * 40, None))

    def test_missing_or_short_commit_cannot_attest_expected_release(self):
        expected = "a" * 40
        missing = self.record()
        missing.pop("definition")
        self.assertFalse(watch._matches_expected_deployment(missing, expected, None))
        shortened = self.record()
        shortened["definition"]["git"]["sha"] = "a" * 12
        self.assertFalse(watch._matches_expected_deployment(shortened, expected, None))
        self.assertFalse(watch._matches_expected_deployment(self.record(), "a" * 12, None))
        self.assertTrue(watch._matches_expected_deployment(self.record(), expected, None))


    def test_missing_or_invalid_deployment_time_cannot_attest_release(self):
        from datetime import UTC, datetime

        expected = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
        for timestamp in (None, "", "not-a-timestamp"):
            item = self.record()
            item.pop("created_at")
            if timestamp is not None:
                item["created_at"] = timestamp
            self.assertFalse(watch._matches_expected_deployment(item, "a" * 40, expected))
        self.assertTrue(watch._matches_expected_deployment(self.record(), "a" * 40, expected))


    def test_updated_time_cannot_substitute_for_creation_time(self):
        from datetime import UTC, datetime

        expected = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
        item = self.record()
        item.pop("created_at")
        item["updated_at"] = "2026-10-04T10:10:00Z"
        self.assertFalse(watch._matches_expected_deployment(item, "a" * 40, expected))
        item["created_at"] = "2026-10-04T09:00:00Z"
        self.assertFalse(watch._matches_expected_deployment(item, "a" * 40, expected))


if __name__ == "__main__":
    unittest.main()


def test_nested_step_commit_cannot_supply_deployment_identity():
    item = {"id": "d1", "status": "healthy", "steps": [{"sha": "a" * 40}]}
    assert not watch._matches_expected_deployment(item, "a" * 40, None)


def test_conflicting_deployment_commits_fail_closed():
    item = {"commit_sha": "a" * 40, "definition": {"git": {"sha": "b" * 40}}}
    assert not watch._matches_expected_deployment(item, "a" * 40, None)
    assert not watch._matches_expected_deployment(item, "b" * 40, None)


def test_provider_outage_has_bounded_retries_and_no_attestation(tmp_path):
    target = tmp_path / "evidence.json"
    with patch.dict(os.environ, {"KOYEB_SERVICE": "service", "KOYEB_TOKEN": "synthetic",
                                "EXPECTED_DEPLOYMENT_SHA": "a" * 40, "EXPECTED_DEPLOYMENT_AFTER": "",
                                "KOYEB_DEPLOYMENT_MAX_ATTEMPTS": "3",
                                "DEPLOYMENT_ATTESTATION_PATH": str(target)}), \
            patch.object(watch, "_deployments", side_effect=RuntimeError("provider unavailable")) as query, \
            patch.object(watch.time, "sleep") as sleep, patch.object(watch, "send_event") as notify:
        assert watch.main() == 1
        assert query.call_count == 3
        assert sleep.call_count == 2
        assert notify.call_count == 1
        assert not target.exists()
