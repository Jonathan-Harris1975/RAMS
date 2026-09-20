"""Deterministic contract tests for the Cloudflare R2 client."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Thread
from types import SimpleNamespace
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError
from botocore.stub import Stubber

from repo_mgmt.r2_client import R2Client, R2Error


class ClosingBody(BytesIO):
    closed_by_client = False

    def close(self) -> None:
        self.closed_by_client = True
        super().close()


def _raw_s3_client(*, endpoint_url: str = "https://r2.invalid"):
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id="unit-test-access-key",
        aws_secret_access_key="unit-test-secret-key",
        region_name="us-east-1",
        config=Config(connect_timeout=1, read_timeout=1, retries={"max_attempts": 1, "mode": "standard"}),
    )


def _wrapper(client: object, *, bucket: str = "audits") -> R2Client:
    wrapper = object.__new__(R2Client)
    wrapper._bucket_audits = bucket
    wrapper._client = client
    return wrapper


def _client_error(code: str, message: str, operation: str, *, status: int = 400) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


def test_init_configures_short_timeouts_and_one_sdk_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    fake_client = object()

    def fake_boto3_client(service: str, **kwargs: object) -> object:
        captured["service"] = service
        captured.update(kwargs)
        return fake_client

    monkeypatch.setattr("repo_mgmt.r2_client.boto3.client", fake_boto3_client)
    cfg = SimpleNamespace(
        r2_bucket_audits="audits",
        r2_endpoint="https://account.r2.cloudflarestorage.com",
        r2_access_key_id="access-key",
        r2_secret_access_key="secret-key",
        r2_region="auto",
    )

    client = R2Client(cfg)

    assert client._bucket_audits == "audits"
    assert client._client is fake_client
    assert captured["service"] == "s3"
    assert captured["endpoint_url"] == cfg.r2_endpoint
    assert captured["aws_access_key_id"] == cfg.r2_access_key_id
    assert captured["aws_secret_access_key"] == cfg.r2_secret_access_key
    config = captured["config"]
    assert isinstance(config, Config)
    assert config.connect_timeout == 3
    assert config.read_timeout == 3
    assert config.retries == {"max_attempts": 1, "mode": "standard"}


class _RetryOnceHandler(BaseHTTPRequestHandler):
    requests_seen = 0

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
        type(self).requests_seen += 1
        self.send_response(500 if type(self).requests_seen == 1 else 200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, _format: str, *args: object) -> None:
        del args


def test_sdk_retry_policy_retries_one_transient_head_bucket_failure() -> None:
    _RetryOnceHandler.requests_seen = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RetryOnceHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        raw = _raw_s3_client(endpoint_url=f"http://127.0.0.1:{server.server_port}")
        raw._endpoint._sleep = lambda _seconds: None
        client = _wrapper(raw)
        assert raw.meta.config.retries == {"mode": "standard", "total_max_attempts": 2}
        assert client.verify_bucket() is True
        assert _RetryOnceHandler.requests_seen == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class TestVerifyBucket:
    def test_successful_head_bucket_uses_configured_audits_bucket(self) -> None:
        raw = _raw_s3_client()
        with Stubber(raw) as stubber:
            stubber.add_response("head_bucket", {}, {"Bucket": "audits"})
            assert _wrapper(raw).verify_bucket() is True

    def test_explicit_bucket_overrides_configured_bucket(self) -> None:
        raw = _raw_s3_client()
        with Stubber(raw) as stubber:
            stubber.add_response("head_bucket", {}, {"Bucket": "other"})
            assert _wrapper(raw).verify_bucket("other") is True

    def test_missing_bucket_configuration_fails_closed_without_sdk_call(self) -> None:
        raw = MagicMock()
        assert _wrapper(raw, bucket="").verify_bucket() is False
        raw.head_bucket.assert_not_called()

    @pytest.mark.parametrize(
        ("code", "status"),
        [("AccessDenied", 403), ("NoSuchBucket", 404)],
    )
    def test_access_or_missing_bucket_returns_false(self, code: str, status: int) -> None:
        raw = _raw_s3_client()
        with Stubber(raw) as stubber:
            stubber.add_client_error(
                "head_bucket",
                service_error_code=code,
                service_message="service detail",
                http_status_code=status,
                expected_params={"Bucket": "audits"},
            )
            assert _wrapper(raw).verify_bucket() is False

    def test_network_or_sdk_readiness_failure_returns_false(self) -> None:
        raw = MagicMock()
        raw.head_bucket.side_effect = EndpointConnectionError(endpoint_url="https://r2.invalid")
        assert _wrapper(raw).verify_bucket() is False

    def test_readiness_log_does_not_echo_service_message(self, caplog: pytest.LogCaptureFixture) -> None:
        raw = MagicMock()
        raw.head_bucket.side_effect = _client_error(
            "AccessDenied",
            "do-not-log-this-secret",
            "HeadBucket",
            status=403,
        )
        assert _wrapper(raw).verify_bucket() is False
        assert "AccessDenied" in caplog.text
        assert "do-not-log-this-secret" not in caplog.text


class TestGetObject:
    def test_successful_read_returns_bytes_and_closes_stream(self) -> None:
        body = ClosingBody(b"payload")
        raw = MagicMock()
        raw.get_object.return_value = {"Body": body, "ContentLength": 7}

        assert _wrapper(raw).get_object("audits", "key.json") == b"payload"
        assert body.closed_by_client is True
        raw.get_object.assert_called_once_with(Bucket="audits", Key="key.json")

    def test_object_not_found_is_translated_without_service_message(self) -> None:
        raw = MagicMock()
        raw.get_object.side_effect = _client_error(
            "NoSuchKey",
            "sensitive-provider-detail",
            "GetObject",
            status=404,
        )
        with pytest.raises(R2Error, match=r"ClientError\(code=NoSuchKey, status=404\)") as exc_info:
            _wrapper(raw).get_object("audits", "missing.json")
        assert "sensitive-provider-detail" not in str(exc_info.value)

    def test_transport_failure_is_domain_specific(self) -> None:
        raw = MagicMock()
        raw.get_object.side_effect = EndpointConnectionError(endpoint_url="https://r2.invalid")
        with pytest.raises(R2Error, match="EndpointConnectionError"):
            _wrapper(raw).get_object("audits", "key.json")


class TestLimitedRead:
    def test_returns_bytes_and_closes_stream(self) -> None:
        body = ClosingBody(b"hello")
        raw = MagicMock()
        raw.get_object.return_value = {"Body": body, "ContentLength": 5}
        assert _wrapper(raw).get_object_limited("audits", "key.json", 5) == b"hello"
        assert body.closed_by_client is True

    def test_rejects_content_length_before_reading(self) -> None:
        body = ClosingBody(b"too large")
        raw = MagicMock()
        raw.get_object.return_value = {"Body": body, "ContentLength": 9}
        with pytest.raises(R2Error, match="exceeds"):
            _wrapper(raw).get_object_limited("audits", "key.json", 4)
        assert body.closed_by_client is True

    def test_rejects_stream_that_exceeds_declared_limit(self) -> None:
        body = ClosingBody(b"abcdef")
        raw = MagicMock()
        raw.get_object.return_value = {"Body": body, "ContentLength": 4}
        with pytest.raises(R2Error, match="exceeds"):
            _wrapper(raw).get_object_limited("audits", "key.json", 4)
        assert body.closed_by_client is True

    def test_stream_read_failure_is_translated_and_stream_is_closed(self) -> None:
        body = MagicMock()
        body.read.side_effect = OSError("do-not-expose-provider-detail")
        raw = MagicMock()
        raw.get_object.return_value = {"Body": body}
        with pytest.raises(R2Error, match="OSError") as exc_info:
            _wrapper(raw).get_object_limited("audits", "key.json", 4)
        assert "do-not-expose-provider-detail" not in str(exc_info.value)
        body.close.assert_called_once_with()


class TestPutObject:
    def test_successful_upload_sends_content_type(self) -> None:
        raw = _raw_s3_client()
        with Stubber(raw) as stubber:
            stubber.add_response(
                "put_object",
                {"ETag": '"unit-test"'},
                {
                    "Bucket": "audits",
                    "Key": "report.json",
                    "Body": b"{}",
                    "ContentType": "application/json",
                },
            )
            _wrapper(raw).put_object(
                "audits", "report.json", b"{}", content_type="application/json"
            )

    def test_upload_sdk_failure_is_domain_specific_and_secret_safe(self) -> None:
        raw = MagicMock()
        raw.put_object.side_effect = _client_error(
            "AccessDenied",
            "do-not-expose-this-secret",
            "PutObject",
            status=403,
        )
        with pytest.raises(R2Error, match=r"ClientError\(code=AccessDenied, status=403\)") as exc_info:
            _wrapper(raw).put_object("audits", "report.json", b"{}", "application/json")
        assert "do-not-expose-this-secret" not in str(exc_info.value)


class TestListObjects:
    def test_returns_keys_from_contents(self) -> None:
        raw = MagicMock()
        raw.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "qa-events/2026-01-01/a.json"},
                {"Key": "qa-events/2026-01-01/b.json"},
            ]
        }
        keys = _wrapper(raw).list_objects("audits", "qa-events/2026-01-01/")
        assert keys == ["qa-events/2026-01-01/a.json", "qa-events/2026-01-01/b.json"]

    def test_returns_empty_list_when_no_contents_key(self) -> None:
        raw = MagicMock()
        raw.list_objects_v2.return_value = {}
        assert _wrapper(raw).list_objects("audits", "qa-events/") == []

    def test_ignores_entries_missing_key(self) -> None:
        raw = MagicMock()
        raw.list_objects_v2.return_value = {
            "Contents": [{"Size": 10}, {"Key": "qa-events/2026-01-01/a.json"}]
        }
        assert _wrapper(raw).list_objects("audits", "qa-events/") == [
            "qa-events/2026-01-01/a.json"
        ]

    def test_passes_bucket_prefix_and_max_keys_through(self) -> None:
        raw = MagicMock()
        raw.list_objects_v2.return_value = {"Contents": []}
        _wrapper(raw).list_objects("audits", "qa-events/2026-01-01/", max_keys=250)
        raw.list_objects_v2.assert_called_once_with(
            Bucket="audits", Prefix="qa-events/2026-01-01/", MaxKeys=250
        )

    def test_wraps_boto_errors_as_r2_error(self) -> None:
        raw = MagicMock()
        raw.list_objects_v2.side_effect = BotoCoreError()
        with pytest.raises(R2Error, match="list_objects failed"):
            _wrapper(raw).list_objects("audits", "qa-events/")


class TestObjectExists:
    def test_existing_object_returns_true(self) -> None:
        raw = _raw_s3_client()
        with Stubber(raw) as stubber:
            stubber.add_response(
                "head_object",
                {"ContentLength": 2, "ContentType": "application/json"},
                {"Bucket": "audits", "Key": "present.json"},
            )
            assert _wrapper(raw).object_exists("audits", "present.json") is True

    @pytest.mark.parametrize("code", ["404", "NoSuchKey"])
    def test_missing_object_returns_false(self, code: str) -> None:
        raw = _raw_s3_client()
        with Stubber(raw) as stubber:
            stubber.add_client_error(
                "head_object",
                service_error_code=code,
                service_message="not found",
                http_status_code=404,
                expected_params={"Bucket": "audits", "Key": "missing.json"},
            )
            assert _wrapper(raw).object_exists("audits", "missing.json") is False

    def test_non_404_head_object_failure_is_translated(self) -> None:
        raw = _raw_s3_client()
        with Stubber(raw) as stubber:
            stubber.add_client_error(
                "head_object",
                service_error_code="AccessDenied",
                service_message="provider detail",
                http_status_code=403,
                expected_params={"Bucket": "audits", "Key": "protected.json"},
            )
            with pytest.raises(R2Error, match=r"ClientError\(code=AccessDenied, status=403\)"):
                _wrapper(raw).object_exists("audits", "protected.json")

    def test_transport_failure_is_translated(self) -> None:
        raw = MagicMock()
        raw.head_object.side_effect = EndpointConnectionError(endpoint_url="https://r2.invalid")
        with pytest.raises(R2Error, match="EndpointConnectionError"):
            _wrapper(raw).object_exists("audits", "object.json")
