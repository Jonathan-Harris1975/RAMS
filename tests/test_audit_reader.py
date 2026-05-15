"""Tests for repo_mgmt.audit_reader."""

from __future__ import annotations

import json
from unittest.mock import MagicMock


from repo_mgmt import audit_reader
from repo_mgmt.r2_client import R2Error


class TestReadLatest:
    def test_returns_parsed_dict_on_success(self, mock_r2: MagicMock) -> None:
        payload = {"findings": [{"title": "test"}]}
        mock_r2.get_object.return_value = json.dumps(payload).encode()
        result = audit_reader.read_latest("on-brand", mock_r2, "audits")
        assert result == payload

    def test_returns_empty_dict_when_key_missing(self, mock_r2: MagicMock) -> None:
        mock_r2.get_object.side_effect = R2Error("404 not found")
        result = audit_reader.read_latest("mobile-ux", mock_r2, "audits")
        assert result == {}

    def test_returns_empty_dict_on_invalid_json(self, mock_r2: MagicMock) -> None:
        mock_r2.get_object.return_value = b"not json {"
        result = audit_reader.read_latest("seo-aeo-geo", mock_r2, "audits")
        assert result == {}

    def test_returns_empty_dict_when_json_is_not_dict(self, mock_r2: MagicMock) -> None:
        mock_r2.get_object.return_value = json.dumps([1, 2, 3]).encode()
        result = audit_reader.read_latest("on-brand", mock_r2, "audits")
        assert result == {}

    def test_passes_correct_key_for_each_pipeline(self, mock_r2: MagicMock) -> None:
        mock_r2.get_object.return_value = b"{}"
        for pid, expected_key in [
            ("seo-aeo-geo", "audits/seo-aeo-geo/latest.json"),
            ("mobile-ux", "audits/mobile-ux/latest.json"),
            ("on-brand", "audits/on-brand/latest.json"),
        ]:
            audit_reader.read_latest(pid, mock_r2, "audits")  # type: ignore[arg-type]
            mock_r2.get_object.assert_called_with(bucket="audits", key=expected_key)

    def test_dereferences_json_artefacts_from_latest_manifest(self, mock_r2: MagicMock) -> None:
        latest = {
            "auditType": "mobile-ux",
            "reportPrefix": "audits/mobile-ux/run-1",
            "repositoryIssueAppendixUrl": "https://public.example/audits/mobile-ux/run-1/repository-issue-appendix.json",
            "artefacts": {
                "responsive-fix-appendix.json": "https://public.example/audits/mobile-ux/run-1/responsive-fix-appendix.json",
                "screenshots/home-320-fail.png": "https://public.example/audits/mobile-ux/run-1/screenshots/home-320-fail.png",
            },
        }
        repo_appendix = {"issues": [{"issueId": "MUX-001"}]}
        responsive = {"rows": [{"issueId": "MUX-002"}]}

        def get_object(*, bucket: str, key: str) -> bytes:
            payloads = {
                "audits/mobile-ux/latest.json": latest,
                "audits/mobile-ux/run-1/repository-issue-appendix.json": repo_appendix,
                "audits/mobile-ux/run-1/responsive-fix-appendix.json": responsive,
            }
            if key not in payloads:
                raise R2Error(f"missing {key}")
            return json.dumps(payloads[key]).encode()

        mock_r2.get_object.side_effect = get_object
        result = audit_reader.read_latest("mobile-ux", mock_r2, "audits")
        assert result["latest"]["auditType"] == "mobile-ux"
        assert result["artefacts"]["repository-issue-appendix.json"] == repo_appendix
        assert result["artefacts"]["responsive-fix-appendix.json"] == responsive
        assert "screenshots/home-320-fail.png" not in result["artefacts"]

    def test_does_not_probe_unlisted_child_artefacts_when_manifest_has_json_urls(
        self, mock_r2: MagicMock
    ) -> None:
        latest = {
            "auditType": "seo-aeo-geo",
            "reportPrefix": "audits/seo-aeo-geo/run-1",
            "summaryUrl": "https://public.example/audits/seo-aeo-geo/run-1/summary.json",
            "coverageUrl": "https://public.example/audits/seo-aeo-geo/run-1/coverage.json",
        }
        payloads = {
            "audits/seo-aeo-geo/latest.json": latest,
            "audits/seo-aeo-geo/run-1/summary.json": {"issueCount": 10},
            "audits/seo-aeo-geo/run-1/coverage.json": {"coveragePercent": 94.8},
        }

        def get_object(*, bucket: str, key: str) -> bytes:
            if key not in payloads:
                raise AssertionError(f"unexpected R2 child probe: {key}")
            return json.dumps(payloads[key]).encode()

        mock_r2.get_object.side_effect = get_object
        result = audit_reader.read_latest("seo-aeo-geo", mock_r2, "audits")
        assert set(result["artefacts"]) == {"summary.json", "coverage.json"}
