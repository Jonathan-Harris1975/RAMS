"""
Report writer for the Repo Management Suite.

Serialises a RunReport to JSON and uploads it to Cloudflare R2.

Report key format:
  <rms_report_prefix>/<pipeline_id>/<run_id>/report.json

JSON shape (camelCase, matching test assertions):
  {
    "runId": str,
    "pipeline": str,
    "dryRun": bool,
    "startedAt": str,
    "finishedAt": str,
    "issuesTotal": int,
    "issuesApplied": int,
    "issuesReverted": int,
    "issuesSkipped": int,
    "issuesFutureGuidance": int,
    "issuesManualReview": int,
    "tasks": [{"taskId": ..., "affectedPaths": ..., ...}],
    "validationCommands": [...],
    "branch": str|null,
    "error": str|null
  }
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
    status: str  # applied | reverted | skipped | future_guidance | manual_review
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
    Serialise *report* to JSON and upload to R2 (or log in dry-run mode).

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
            pipeline_id, len(body), key,
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
        pipeline_id, len(body), key,
    )
    return key


def make_run_id() -> str:
    """Return a URL-safe UTC run ID: YYYY-MM-DDTHH-MM-SSZ."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


# ── Serialisation ──────────────────────────────────────────────────────────


def _to_dict(report: RunReport) -> dict[str, Any]:
    """Serialise RunReport to a camelCase JSON-ready dict."""
    return {
        "runId": report.run_id,
        "pipeline": report.pipeline,
        "dryRun": report.dry_run,
        "startedAt": report.started_at,
        "finishedAt": report.finished_at,
        "issuesTotal": report.issues_total,
        "issuesApplied": report.issues_applied,
        "issuesReverted": report.issues_reverted,
        "issuesSkipped": report.issues_skipped,
        "issuesFutureGuidance": report.issues_future_guidance,
        "issuesManualReview": report.issues_manual_review,
        "tasks": [_task_to_camel(t) for t in report.tasks],
        "validationCommands": report.validation_commands,
        "branch": report.branch,
        "error": report.error,
    }


def _task_to_camel(t: TaskReport) -> dict[str, Any]:
    """Convert a TaskReport to a camelCase dict."""
    return {
        "taskId": t.task_id,
        "classification": t.classification,
        "status": t.status,
        "affectedPaths": t.affected_paths,
        "patchPlanOps": t.patch_plan_ops,
        "validationPassed": t.validation_passed,
        "commitSha": t.commit_sha,
        "error": t.error,
    }
