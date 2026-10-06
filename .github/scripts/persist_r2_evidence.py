#!/usr/bin/env python3
"""Persist sanitised GitHub workflow evidence to the ecosystem R2 evidence bucket."""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import boto3


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"required environment variable {name} is missing")
    return value


repository = required("GITHUB_REPOSITORY")
target_sha = required("TARGET_SHA")
run_id = required("GITHUB_RUN_ID")
run_attempt = required("GITHUB_RUN_ATTEMPT")
kind = required("EVIDENCE_KIND").lower()
bucket = required("EVIDENCE_BUCKET")
endpoint = required("R2_ENDPOINT")
access_key = required("R2_ACCESS_KEY_ID")
secret_key = required("R2_SECRET_ACCESS_KEY")
status = os.getenv("EVIDENCE_STATUS", "unknown").strip() or "unknown"
certification_state = os.getenv("CERTIFICATION_STATE", "").strip()
patterns = [p.strip() for p in os.getenv("EVIDENCE_FILES", "").split(",") if p.strip()]

prefix = f"{repository}/{target_sha}/{kind}/{run_id}/{run_attempt}"
metadata = {
    "schemaVersion": "hive-repository-evidence/v1",
    "repository": repository,
    "defaultBranchSha": target_sha,
    "workflowRunId": run_id,
    "workflowRunAttempt": run_attempt,
    "evidenceKind": kind,
    "workflowStatus": status,
    "generatedAt": datetime.now(timezone.utc).isoformat(),
}
if certification_state:
    metadata["certificationState"] = certification_state

client = boto3.client(
    "s3",
    endpoint_url=endpoint,
    aws_access_key_id=access_key,
    aws_secret_access_key=secret_key,
    region_name="auto",
)

payload = json.dumps(metadata, sort_keys=True, indent=2).encode("utf-8")
client.put_object(
    Bucket=bucket,
    Key=f"{prefix}/metadata.json",
    Body=payload,
    ContentType="application/json",
)

uploaded = ["metadata.json"]
seen: set[str] = set()
for pattern in patterns:
    for item in sorted(glob.glob(pattern)):
        path = Path(item)
        if not path.is_file():
            continue
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        key = f"{prefix}/{path.name}"
        client.upload_file(str(path), bucket, key)
        uploaded.append(path.name)

print(json.dumps({"bucket": bucket, "prefix": prefix, "uploaded": uploaded}, sort_keys=True))
