"""
Report publisher for the Repo Management Suite.

Writes RunReport JSON in one of two modes:

  dry_run=True  -> local file only:
                    ./dry-run-<pipeline_id>-report.json

  dry_run=False -> two R2 objects:
                    qa-suite/reports/<pipeline>/<runId>/report.json
                    qa-suite/reports/<pipeline>/latest.json

RunReport schema (matches briefing §9):
  {
    "runId": str,
    "pipeline": PipelineId,
    "targetRepo": str,
    "branch": str,
    "dryRun": bool,
    "summary": {
      "snapshotsRead": int,
      "tasksGenerated": int,
      "codeFixesAttempted": int,
      "committed": int,
      "validationFailed": int,
      "futureGuidance": int,
      "manualReview": int
    },
    "tasks": list[NormalisedIssue],
    "validation": {
      "commands": list[str],
      "passed": bool,
      "outputTail": str
    } | null,
    "commits": [{"sha": str, "message": str, "files": list[str]}]
  }
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repo_mgmt.config import Settings
    from repo_mgmt.r2_client import R2Client

logger = logging.getLogger(__name__)


# ── Data models ────────────────────────────────────────────────────────────


@dataclass
class CommitInfo:
    """Per-commit metadata within a RunReport."""
    sha: str
    message: str
    files: list[str] = field(default_factory=list)


@dataclass
class ValidationSummary:
    """Validation section of a RunReport."""
    commands: list[str]
    passed: bool
    output_tail: str = ""


@dataclass
class RunReport:
    """Top-level report for a single pipeline run."""
    runId: str
    pipeline: str
    targetRepo: str
    branch: str
    dryRun: bool
    summary: dict[str, int] = field(default_factory=dict)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    validation: ValidationSummary | None = None
    commits: list[CommitInfo] = field(default_factory=list)


def make_run_id() -> str:
    """Return a new ISO-UTC run identifier safe for use in paths."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


# ── Publisher ──────────────────────────────────────────────────────────────


def publish(
    report: RunReport,
    cfg: "Settings",
    r2: "R2Client",
) -> str:
    """
    Serialise *report* and write it to the appropriate destination.

    dry_run=True  -> ./dry-run-<pipeline>-report.json (local)
    dry_run=False -> R2: qa-suite/reports/<pipeline>/<runId>/report.json
                         qa-suite/reports/<pipeline>/latest.json

    Returns:
        Destination description (local path or primary R2 key).
    """
    payload = _serialise(report)

    if report.dryRun:
        local_path = Path(f"dry-run-{report.pipeline}-report.json")
        local_path.write_text(payload, encoding="utf-8")
        logger.info("report_publisher: [dry-run] wrote %s", local_path)
        return str(local_path)

    bucket = cfg.r2_bucket_audits
    prefix = cfg.rms_report_prefix
    run_key = f"{prefix}/{report.pipeline}/{report.runId}/report.json"
    latest_key = f"{prefix}/{report.pipeline}/latest.json"

    body_bytes = payload.encode("utf-8")
    r2.put_object(
        bucket=bucket, key=run_key, body=body_bytes, content_type="application/json"
    )
    r2.put_object(
        bucket=bucket, key=latest_key, body=body_bytes, content_type="application/json"
    )

    logger.info(
        "report_publisher: uploaded report to R2: %r and %r", run_key, latest_key
    )
    return run_key


# ── Serialisation ──────────────────────────────────────────────────────────


def _serialise(report: RunReport) -> str:
    """Convert *report* to canonical camelCase JSON."""

    def _convert(obj: Any) -> Any:
        if isinstance(obj, CommitInfo):
            return {"sha": obj.sha, "message": obj.message, "files": obj.files}
        if isinstance(obj, ValidationSummary):
            return {
                "commands": obj.commands,
                "passed": obj.passed,
                "outputTail": obj.output_tail,
            }
        if isinstance(obj, RunReport):
            return {
                "runId": obj.runId,
                "pipeline": obj.pipeline,
                "targetRepo": obj.targetRepo,
                "branch": obj.branch,
                "dryRun": obj.dryRun,
                "summary": obj.summary,
                "tasks": [_convert(t) for t in obj.tasks],
                "validation": _convert(obj.validation) if obj.validation else None,
                "commits": [_convert(c) for c in obj.commits],
            }
        if isinstance(obj, dict):
            return {k: _convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_convert(i) for i in obj]
        return obj

    return json.dumps(_convert(report), indent=2)
