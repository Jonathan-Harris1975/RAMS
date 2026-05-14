"""Durable report publisher for the Repo Management Suite."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from repo_mgmt.schemas import RunReportModel

if TYPE_CHECKING:
    from repo_mgmt.config import Settings
    from repo_mgmt.r2_client import R2Client

logger = logging.getLogger(__name__)


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
class PublishStatus:
    """Durability metadata for report publication attempts."""

    destination: str = "not_attempted"
    ok: bool = False
    error: str | None = None
    fallback_path: str | None = None


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
    validation: ValidationSummary = field(
        default_factory=lambda: ValidationSummary(
            commands=[],
            passed=False,
            output_tail="not_run: validation did not run",
        )
    )
    commits: list[CommitInfo] = field(default_factory=list)
    error: str | None = None
    publish_status: PublishStatus = field(default_factory=PublishStatus)


def make_run_id() -> str:
    """Return a new ISO-UTC run identifier safe for use in paths."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def publish(report: RunReport, cfg: "Settings", r2: "R2Client") -> str:
    """
    Serialise *report* and write it to the appropriate destination.

    Dry-run writes locally under cfg.rms_report_dir. Live mode writes the run
    report and latest pointer to R2. R2 errors are deliberately propagated so
    callers can record a fallback report and log the stack trace.
    """
    if report.dryRun:
        local_path = _local_report_path(report, cfg, prefix="dry-run")
        report.publish_status = PublishStatus(destination=str(local_path), ok=True)
        payload = _serialise(report)
        _write_text(local_path, payload)
        logger.info("report_publisher: [dry-run] wrote %s", local_path)
        return str(local_path)

    bucket = cfg.r2_bucket_audits
    prefix = cfg.rms_report_prefix
    run_key = f"{prefix}/{report.pipeline}/{report.runId}/report.json"
    latest_key = f"{prefix}/{report.pipeline}/latest.json"
    report.publish_status = PublishStatus(destination=run_key, ok=True)
    payload = _serialise(report)

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


def write_local_fallback(
    report: RunReport,
    cfg: "Settings",
    reason: str,
) -> str:
    """Write a local fallback report after a failed publish attempt."""
    fallback_path = _local_report_path(report, cfg, prefix="fallback")
    report.publish_status = PublishStatus(
        destination="local_fallback",
        ok=False,
        error=reason,
        fallback_path=str(fallback_path),
    )
    _write_text(fallback_path, _serialise(report))
    logger.error("report_publisher: wrote fallback report to %s", fallback_path)
    return str(fallback_path)


def _local_report_path(report: RunReport, cfg: "Settings", *, prefix: str) -> Path:
    """Build a local report path containing pipeline and run ID."""
    safe_pipeline = report.pipeline.replace("/", "-")
    safe_run_id = report.runId.replace("/", "-")
    return cfg.report_dir() / f"{prefix}-{safe_pipeline}-{safe_run_id}-report.json"


def _write_text(path: Path, payload: str) -> None:
    """Create parent directories and write UTF-8 JSON payload."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _serialise(report: RunReport) -> str:
    """Convert *report* to validated canonical camelCase JSON."""
    data = _convert(report)
    try:
        RunReportModel.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"RunReport failed strict schema validation: {exc}") from exc
    return json.dumps(data, indent=2)


def _convert(obj: Any) -> Any:
    """Convert report dataclasses and nested values to JSON-ready objects."""
    if isinstance(obj, CommitInfo):
        return {"sha": obj.sha, "message": obj.message, "files": obj.files}
    if isinstance(obj, ValidationSummary):
        return {
            "commands": obj.commands,
            "passed": obj.passed,
            "outputTail": obj.output_tail,
        }
    if isinstance(obj, PublishStatus):
        data: dict[str, Any] = {"destination": obj.destination, "ok": obj.ok}
        if obj.error is not None:
            data["error"] = obj.error
        if obj.fallback_path is not None:
            data["fallbackPath"] = obj.fallback_path
        return data
    if isinstance(obj, RunReport):
        data = {
            "runId": obj.runId,
            "pipeline": obj.pipeline,
            "targetRepo": obj.targetRepo,
            "branch": obj.branch,
            "dryRun": obj.dryRun,
            "summary": obj.summary,
            "tasks": [_convert(task) for task in obj.tasks],
            "validation": _convert(obj.validation),
            "commits": [_convert(commit) for commit in obj.commits],
            "publishStatus": _convert(obj.publish_status),
        }
        if obj.error is not None:
            data["error"] = obj.error
        return data
    if isinstance(obj, dict):
        return {str(key): _convert(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_convert(item) for item in obj]
    return obj
