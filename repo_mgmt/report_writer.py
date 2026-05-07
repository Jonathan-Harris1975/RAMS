"""
Report writer for the Repo Management Suite.

Serialises a RunReport to JSON and uploads it to Cloudflare R2 under the
prefix defined in RMS_REPORT_PREFIX.

Report key format:
  <rms_report_prefix>/<pipeline_id>/<run_id>/report.json

e.g.:
  qa-suite/reports/on-brand/2026-05-05T03-00-00Z/report.json
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId, Settings
    from repo_mgmt.r2_client import R2Client

logger = logging.getLogger(__name__)


@dataclass
class TaskReport:
    """Per-task outcome within a RunReport."""

    task_id: str
    classification: str
    status: str                # applied | reverted | skipped | future_guidance | manual_review
    affected_paths: list[str] = field(default_factory=list)
    patch_plan_ops: int = 0
    validation_passed: bool | None = None
    commit_sha: str | None = None
    error: str | None = None


@dataclass
class RunReport:
    """Top-level report produced by a single pipeline run."""

    run_id: str
    pipeline: str
    dry_run: bool
    started_at: str
    finished_at: str
    issues_total: int
    issues_applied: int
    issues_reverted: int
    issues_skipped: int
    issues_future_guidance: int
    issues_manual_review: int
    tasks: list[TaskReport] = field(default_factory=list)
    validation_commands: list[str] = field(default_factory=list)
    branch: str | None = None
    error: str | None = None


def write(
    report: RunReport,
    pipeline_id: "PipelineId",
    cfg: "Settings",
    r2: "R2Client",
    dry_run: bool = True,
) -> str:
    """
    Serialise *report* to JSON and upload to R2.

    Args:
        report: Completed RunReport from the pipeline.
        pipeline_id: Pipeline that was run.
        cfg: Validated RMS settings.
        r2: Initialised R2Client.
        dry_run: If True, log the report key but skip the upload.

    Returns:
        The R2 key where the report was (or would be) uploaded.
    """
    key = (
        f"{cfg.rms_report_prefix.rstrip('/')}/"
        f"{pipeline_id}/"
        f"{report.run_id}/"
        f"report.json"
    )

    payload: dict[str, Any] = _to_dict(report)
    body = json.dumps(payload, indent=2, default=str).encode("utf-8")

    if dry_run:
        logger.info(
            "report_writer [%s]: dry-run — would upload %d-byte report to %r",
            pipeline_id,
            len(body),
            key,
        )
        return key

    r2.put_object(
        bucket=cfg.r2_bucket_audits,
        key=key,
        body=body,
        content_type="application/json",
    )
    logger.info(
        "report_writer [%s]: uploaded %d-byte report to %r",
        pipeline_id,
        len(body),
        key,
    )
    return key


def make_run_id() -> str:
    """
    Return a URL-safe run ID string based on the current UTC time.

    Format: YYYY-MM-DDTHH-MM-SSZ
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H-%M-%SZ")


def _to_dict(report: RunReport) -> dict[str, Any]:
    """Convert a RunReport dataclass to a plain dict for JSON serialisation."""
    d = asdict(report)
    # Convert nested TaskReport dicts to camelCase keys for API consistency
    d["tasks"] = [_task_to_camel(t) for t in d.get("tasks", [])]
    return d


def _task_to_camel(t: dict[str, Any]) -> dict[str, Any]:
    """Convert a TaskReport dict's snake_case keys to camelCase."""
    return {
        "taskId": t["task_id"],
        "classification": t["classification"],
        "status": t["status"],
        "affectedPaths": t["affected_paths"],
        "patchPlanOps": t["patch_plan_ops"],
        "validationPassed": t["validation_passed"],
        "commitSha": t["commit_sha"],
        "error": t["error"],
    }
