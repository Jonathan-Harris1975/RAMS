"""Tests for repo_mgmt.audit_reader."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

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
