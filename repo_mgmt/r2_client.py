"""
Cloudflare R2 client for the Repo Management Suite.

Wraps boto3 S3-compatible calls against the R2 endpoint.
Raises R2Error (a custom exception) on any boto3 ClientError so callers
never need to import boto3 directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import ClientError

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

        Args:
            cfg: Validated RMS settings object.
        """
        self._bucket_audits = cfg.r2_bucket_audits
        self._client = boto3.client(
            "s3",
            endpoint_url=cfg.r2_endpoint,
            aws_access_key_id=cfg.r2_access_key_id,
            aws_secret_access_key=cfg.r2_secret_access_key,
            region_name=cfg.r2_region,
        )

    # ── Public API ─────────────────────────────────────────────────────────

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
            return response["Body"].read()  # type: ignore[no-any-return]
        except ClientError as exc:
            raise R2Error(
                f"R2 get_object failed for bucket={bucket!r} key={key!r}: {exc}"
            ) from exc

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
        except ClientError as exc:
            raise R2Error(
                f"R2 put_object failed for bucket={bucket!r} key={key!r}: {exc}"
            ) from exc

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
