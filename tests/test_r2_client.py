"""Tests for bounded Cloudflare R2 reads."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock

import pytest

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
