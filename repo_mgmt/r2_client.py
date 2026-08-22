"""
Cloudflare R2 client for the Repo Management Suite.

Wraps boto3 S3-compatible calls against the R2 endpoint. Client construction is
not treated as readiness: callers must use ``verify_bucket`` when they need to
prove that the configured endpoint, credentials, and bucket are reachable.
Raises R2Error on boto3 failures so callers never need to import boto3 directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError

if TYPE_CHECKING:
    from repo_mgmt.config import Settings

logger = logging.getLogger(__name__)


class R2Error(Exception):
    """Raised when a Cloudflare R2 operation fails."""


class R2Client:
    """Thin wrapper around boto3 for Cloudflare R2 object storage."""

    def __init__(self, cfg: "Settings") -> None:
        """
        Initialise the R2 client using credentials from *cfg*.

        Construction only validates local configuration shape. It does not prove
        network reachability or credential validity. Use ``verify_bucket`` for a
        lightweight live readiness check.
        """
        self._bucket_audits = cfg.r2_bucket_audits
        self._client = boto3.client(
            "s3",
            endpoint_url=cfg.r2_endpoint,
            aws_access_key_id=cfg.r2_access_key_id,
            aws_secret_access_key=cfg.r2_secret_access_key,
            region_name=cfg.r2_region,
            config=Config(
                connect_timeout=3,
                read_timeout=3,
                retries={"max_attempts": 1, "mode": "standard"},
            ),
        )

    # ── Public API ─────────────────────────────────────────────────────────

    def verify_bucket(self, bucket: str | None = None) -> bool:
        """
        Return True only when the configured R2 bucket can be reached.

        The check uses S3 ``HeadBucket`` against the audits bucket. Invalid
        endpoints, bad credentials, permission failures, and missing buckets all
        return False instead of raising so /readiness can report a degraded state
        without disturbing /health liveness.
        """
        target_bucket = bucket or self._bucket_audits
        if not target_bucket:
            return False
        try:
            self._client.head_bucket(Bucket=target_bucket)
            return True
        except (ClientError, EndpointConnectionError, BotoCoreError) as exc:
            logger.warning("r2_client: bucket readiness probe failed: %s", exc)
            return False

    def get_object(self, bucket: str, key: str) -> bytes:
        """
        Retrieve an object from R2 and return its raw bytes.

        Args:
            bucket: R2 bucket name.
            key: Object key (path within the bucket).

        Returns:
            Raw bytes of the object body.

        Raises:
            R2Error: If the boto3 call fails for any reason.
        """
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            body: bytes = response["Body"].read()
            return body
        except (ClientError, EndpointConnectionError, BotoCoreError) as exc:
            raise R2Error(
                f"R2 get_object failed for bucket={bucket!r} key={key!r}: {exc}"
            ) from exc

    def get_object_limited(self, bucket: str, key: str, max_bytes: int) -> bytes:
        """Retrieve at most ``max_bytes`` and reject oversized R2 objects."""
        body_stream: object | None = None
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            content_length = response.get("ContentLength")
            body_stream = response["Body"]
            if isinstance(content_length, int) and content_length > max_bytes:
                raise R2Error(
                    f"R2 object exceeds {max_bytes} bytes for bucket={bucket!r} key={key!r}"
                )
            read = getattr(body_stream, "read")
            body = bytes(read(max_bytes + 1))
            if len(body) > max_bytes:
                raise R2Error(
                    f"R2 object exceeds {max_bytes} bytes for bucket={bucket!r} key={key!r}"
                )
            return body
        except R2Error:
            raise
        except (ClientError, EndpointConnectionError, BotoCoreError, OSError) as exc:
            raise R2Error(
                f"R2 get_object failed for bucket={bucket!r} key={key!r}: {exc}"
            ) from exc
        finally:
            close = getattr(body_stream, "close", None)
            if callable(close):
                close()

    def put_object(
        self,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        """
        Upload *body* to R2 at the given *bucket* / *key*.

        Args:
            bucket: R2 bucket name.
            key: Destination object key.
            body: Raw bytes to upload.
            content_type: MIME type for the object.

        Raises:
            R2Error: If the boto3 call fails for any reason.
        """
        try:
            self._client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
            )
        except (ClientError, EndpointConnectionError, BotoCoreError) as exc:
            raise R2Error(
                f"R2 put_object failed for bucket={bucket!r} key={key!r}: {exc}"
            ) from exc

    def list_objects(self, bucket: str, prefix: str, *, max_keys: int = 1000) -> list[str]:
        """
        List object keys under *prefix* in *bucket*.

        Bounded to a single ``ListObjectsV2`` call (no pagination) so this
        stays cheap on the eMicro instance. Callers expecting more than
        *max_keys* results should narrow the prefix (e.g. to a single day)
        rather than paginating.

        Args:
            bucket: R2 bucket name.
            prefix: Key prefix to list under.
            max_keys: Maximum number of keys to return (<=1000 per call).

        Returns:
            Object keys matching the prefix, in the order R2 returns them.

        Raises:
            R2Error: If the boto3 call fails for any reason.
        """
        try:
            response = self._client.list_objects_v2(
                Bucket=bucket, Prefix=prefix, MaxKeys=max_keys
            )
        except (ClientError, EndpointConnectionError, BotoCoreError) as exc:
            raise R2Error(
                f"R2 list_objects failed for bucket={bucket!r} prefix={prefix!r}: {exc}"
            ) from exc
        contents = response.get("Contents", []) or []
        return [str(item["Key"]) for item in contents if "Key" in item]

    def object_exists(self, bucket: str, key: str) -> bool:
        """
        Return True if an object exists at *bucket* / *key*, False otherwise.

        Args:
            bucket: R2 bucket name.
            key: Object key to check.

        Raises:
            R2Error: If the boto3 call fails for reasons other than a 404.
        """
        try:
            self._client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                return False
            raise R2Error(
                f"R2 object_exists failed for bucket={bucket!r} key={key!r}: {exc}"
            ) from exc
        except (EndpointConnectionError, BotoCoreError) as exc:
            raise R2Error(
                f"R2 object_exists failed for bucket={bucket!r} key={key!r}: {exc}"
            ) from exc
