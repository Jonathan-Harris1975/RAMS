"""
Report publisher for the Repo Management Suite.

Writes RunReport JSON in one of two modes:

  dry_run=True  → local file only:
                    ./dry-run-<pipeline_id>-report.json

  dry_run=False → two R2 objects:
                    qa-suite/reports/<pipeline_id>/<runId>/report.json
                    qa-suite/reports/<pipeline_id>/latest.json

Uses R2_BUCKET_AUDITS and R2_PUBLIC_BASE_URL_AUDITS from config.
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
class TaskReport:
    """Per-task outcome within a RunReport."""
    task_id: str
    classification: str
    status: str            # applied | reverted | skipped | future_guidance | manual_review
    affected_paths: list[str] = field(default_factory=list)
    patch_ops: int = 0
    validation_passed: bool | None = None
    commit_sha: str | None = None
    error: str | None = None


@dataclass
class ValidationSummary:
    """Summary of the final validation run."""
    passed: bool
    output_tail: str = ""


@dataclass
class RunReport:
    """Top-level report for a single pipeline run."""
    runId: str
    pipeline: str
    targetRepo: str
    branch: str | None
    dryRun: bool
    summary: dict[str, int] = field(default_factory=dict)
    tasks: list[TaskReport] = field(default_factory=list)
    validation: ValidationSummary | None = None
    commits: list[str] = field(default_factory=list)


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

    Args:
        report: Completed RunReport dataclass.
        cfg: Validated RMS settings.
        r2: Initialised R2Client (only used when dry_run=False).

    Returns:
        Destination description string (local path or R2 key).
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

    r2.put_object(bucket=bucket, key=run_key, body=payload, content_type="application/json")
    r2.put_object(bucket=bucket, key=latest_key, body=payload, content_type="application/json")

    logger.info(
        "report_publisher: uploaded report to R2: %r and %r", run_key, latest_key
    )
    return run_key


def _serialise(report: RunReport) -> str:
    """Convert *report* to a JSON string, handling nested dataclasses."""
    def _to_dict(obj: Any) -> Any:
        if hasattr(obj, "__dataclass_fields__"):
            return {k: _to_dict(v) for k, v in asdict(obj).items()}
        if isinstance(obj, list):
            return [_to_dict(i) for i in obj]
        return obj

    return json.dumps(_to_dict(report), indent=2)
