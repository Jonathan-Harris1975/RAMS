"""Tests for bounded Cloudflare R2 reads."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import BotoCoreError

from repo_mgmt.r2_client import R2Client, R2Error


class ClosingBody(BytesIO):
    closed_by_client = False

    def close(self) -> None:
        self.closed_by_client = True
        super().close()


def _client_with_response(body: ClosingBody, content_length: int) -> R2Client:
    client = object.__new__(R2Client)
    client._bucket_audits = "audits"
    client._client = MagicMock()
    client._client.get_object.return_value = {
        "Body": body,
        "ContentLength": content_length,
    }
    return client


def test_limited_read_returns_bytes_and_closes_stream() -> None:
    body = ClosingBody(b"hello")
    client = _client_with_response(body, 5)
    assert client.get_object_limited("audits", "key.json", 5) == b"hello"
    assert body.closed_by_client is True


def test_limited_read_rejects_content_length_before_reading() -> None:
    body = ClosingBody(b"too large")
    client = _client_with_response(body, 9)
    with pytest.raises(R2Error, match="exceeds"):
        client.get_object_limited("audits", "key.json", 4)
    assert body.closed_by_client is True


def test_limited_read_rejects_stream_that_exceeds_declared_limit() -> None:
    body = ClosingBody(b"abcdef")
    client = _client_with_response(body, 4)
    with pytest.raises(R2Error, match="exceeds"):
        client.get_object_limited("audits", "key.json", 4)
    assert body.closed_by_client is True


def _client_with_list_response(contents: list[dict]) -> R2Client:
    client = object.__new__(R2Client)
    client._bucket_audits = "audits"
    client._client = MagicMock()
    client._client.list_objects_v2.return_value = {"Contents": contents}
    return client


class TestListObjects:
    def test_returns_keys_from_contents(self) -> None:
        client = _client_with_list_response(
            [{"Key": "qa-events/2026-01-01/a.json"}, {"Key": "qa-events/2026-01-01/b.json"}]
        )
        keys = client.list_objects("audits", "qa-events/2026-01-01/")
        assert keys == ["qa-events/2026-01-01/a.json", "qa-events/2026-01-01/b.json"]

    def test_returns_empty_list_when_no_contents_key(self) -> None:
        client = object.__new__(R2Client)
        client._bucket_audits = "audits"
        client._client = MagicMock()
        client._client.list_objects_v2.return_value = {}
        assert client.list_objects("audits", "qa-events/") == []

    def test_ignores_entries_missing_key(self) -> None:
        client = _client_with_list_response([{"Size": 10}, {"Key": "qa-events/2026-01-01/a.json"}])
        assert client.list_objects("audits", "qa-events/") == ["qa-events/2026-01-01/a.json"]

    def test_passes_bucket_prefix_and_max_keys_through(self) -> None:
        client = _client_with_list_response([])
        client.list_objects("audits", "qa-events/2026-01-01/", max_keys=250)
        client._client.list_objects_v2.assert_called_once_with(
            Bucket="audits", Prefix="qa-events/2026-01-01/", MaxKeys=250
        )

    def test_wraps_boto_errors_as_r2_error(self) -> None:
        client = object.__new__(R2Client)
        client._bucket_audits = "audits"
        client._client = MagicMock()
        client._client.list_objects_v2.side_effect = BotoCoreError()
        with pytest.raises(R2Error, match="list_objects failed"):
            client.list_objects("audits", "qa-events/")
