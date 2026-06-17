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
from repo_mgmt.phase5_skills import phase5_skills_summary

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
    failed_command: str | None = None
    return_code: int | None = None
    affected_repo: str | None = None
    actionable_hint: str | None = None
    patching_skipped: bool | None = None


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
    baseline_validation: ValidationSummary | None = None
    commits: list[CommitInfo] = field(default_factory=list)
    error: str | None = None
    publish_status: PublishStatus = field(default_factory=PublishStatus)
    skills_baseline: dict[str, Any] | None = None
    ai_usage: dict[str, Any] | None = None
    release_evidence: dict[str, Any] | None = None


def _report_quality(report: "RunReport") -> dict[str, Any]:
    """Return operator-facing quality gates for Lane 1 audit reports."""
    pipeline_gates: dict[str, dict[str, Any]] = {
        "seo-aeo-geo": {
            "primaryGoal": "Verify search, answer-engine, and generative-engine discovery surfaces before patching.",
            "requiredEvidence": [
                "dynamic-route-manifest.json",
                "sitemap.xml",
                "llms.txt",
                "llm-index.json",
                "search-visibility-baseline.json",
            ],
            "blockedIfMissing": [
                "canonical dynamic routes",
                "transcript URLs in sitemap/discovery outputs",
                "full-estate LLM discovery coverage",
            ],
        },
        "mobile-ux": {
            "primaryGoal": "Verify rendered mobile behaviour with screenshots before declaring release readiness.",
            "requiredEvidence": [
                "rendered browser automation",
                "mobile viewport emulation",
                "screenshot manifest",
                "per-viewport root-cause grouping",
                "accessibility-appendix.json",
                "WCAG 2.2 AA accessibility evidence",
            ],
            "blockedIfMissing": [
                "screenshots",
                "viewport matrix",
                "exact affected route/component mapping",
                "accessibility evidence rows for rendered route/viewports",
            ],
        },
        "on-brand": {
            "primaryGoal": "Verify future-output brand guardrails across blog, RSS, and podcast copy.",
            "requiredEvidence": [
                "evidence.json",
                "confirmed defects ledger",
                "future QA remediation plan",
            ],
            "blockedIfMissing": [
                "LLM judgement when dryRun=false",
                "source-level examples",
                "pipeline-level smallest safe fixes",
            ],
        },
    }
    gate = pipeline_gates.get(report.pipeline, {})
    summary = report.summary or {}
    tasks = report.tasks or []
    validation_failed = bool(summary.get("validationFailed")) or not bool(
        report.validation.passed
    )
    code_fix_tasks = [
        task for task in tasks if task.get("classification") == "code_fix"
    ]
    manual_tasks = [
        task
        for task in tasks
        if task.get("classification") == "manual_review"
        or task.get("status") == "manual_review"
    ]
    return {
        "lane": "Lane 1 autonomous reporting",
        "status": "approval_required"
        if report.dryRun or code_fix_tasks or manual_tasks or validation_failed
        else "report_only_clear",
        "manualInterventionRequired": bool(
            report.dryRun or code_fix_tasks or manual_tasks or validation_failed
        ),
        "dryRun": report.dryRun,
        "primaryGoal": gate.get(
            "primaryGoal", "Verify audit evidence before any production mutation."
        ),
        "requiredEvidence": gate.get("requiredEvidence", []),
        "blockedIfMissing": gate.get("blockedIfMissing", []),
        "qualitySignals": {
            "tasksGenerated": int(summary.get("tasksGenerated", len(tasks))),
            "codeFixCandidates": int(summary.get("codeFixCandidates", 0)),
            "manualReview": int(summary.get("manualReview", 0)),
            "validationPassed": bool(report.validation.passed),
            "baselineValidationPassed": None
            if report.baseline_validation is None
            else bool(report.baseline_validation.passed),
        },
        "operatorNextStep": _operator_next_step(
            report, validation_failed, bool(code_fix_tasks), bool(manual_tasks)
        ),
        "phase5Skills": phase5_skills_summary(report.pipeline),
    }


def _operator_next_step(
    report: "RunReport", validation_failed: bool, has_code_fix: bool, has_manual: bool
) -> str:
    """Return one concise next action for a RAMS operator."""
    if validation_failed:
        return "Fix validation failures before applying or merging generated changes."
    if report.dryRun:
        return "Review the dry-run plan, then rerun live mode only when the baseline is clean."
    if has_code_fix:
        return "Review the generated code-fix branch or pull request before production merge."
    if has_manual:
        return "Triage manual-review tasks before starting another remediation run."
    return "No immediate manual action from the report metadata."


def make_run_id() -> str:
    """Return a new ISO-UTC run identifier safe for use in paths."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _attach_release_evidence(report: RunReport, cfg: "Settings") -> None:
    """Attach bounded release metadata without credentials or provider payloads."""
    report.release_evidence = {
        "releaseId": cfg.rms_release_id or None,
        "reportRetentionDays": cfg.rms_report_retention_days,
        "publishedAt": datetime.now(tz=timezone.utc).isoformat(),
        "targetBranch": report.branch,
        "dryRun": report.dryRun,
    }


def publish(report: RunReport, cfg: "Settings", r2: "R2Client") -> str:
    """
    Serialise *report* and write it to the appropriate destination.

    Dry-run writes locally under cfg.rms_report_dir. Live mode writes the run
    report and latest pointer to R2. R2 errors are deliberately propagated so
    callers can record a fallback report and log the stack trace.
    """
    _attach_release_evidence(report, cfg)

    if report.dryRun:
        local_path = _local_report_path(report, cfg, prefix="dry-run")
        report.publish_status = PublishStatus(destination=str(local_path), ok=True)
        payload = _serialise(report, max_bytes=cfg.rms_report_max_bytes)
        _write_text(local_path, payload)
        logger.info("report_publisher: [dry-run] wrote %s", local_path)
        return str(local_path)

    bucket = cfg.r2_bucket_audits
    prefix = cfg.rms_report_prefix
    run_key = f"{prefix}/{report.pipeline}/{report.runId}/report.json"
    latest_key = f"{prefix}/{report.pipeline}/latest.json"
    report.publish_status = PublishStatus(destination=run_key, ok=True)
    payload = _serialise(report, max_bytes=cfg.rms_report_max_bytes)

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
    _attach_release_evidence(report, cfg)
    fallback_path = _local_report_path(report, cfg, prefix="fallback")
    report.publish_status = PublishStatus(
        destination="local_fallback",
        ok=False,
        error=reason,
        fallback_path=str(fallback_path),
    )
    _write_text(fallback_path, _serialise(report, max_bytes=cfg.rms_report_max_bytes))
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


def _serialise(report: RunReport, max_bytes: int | None = None) -> str:
    """Convert *report* to validated JSON and enforce an optional size ceiling."""
    data = _convert(report)
    try:
        RunReportModel.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"RunReport failed strict schema validation: {exc}") from exc
    payload = json.dumps(data, indent=2)
    if max_bytes is None or len(payload.encode("utf-8")) <= max_bytes:
        return payload
    bounded = _bound_report_value(data)
    if isinstance(bounded, dict):
        bounded["reportTruncated"] = True
        bounded["reportTruncationReason"] = (
            f"Report exceeded configured {max_bytes} byte limit"
        )
    payload = json.dumps(bounded, indent=2)
    if len(payload.encode("utf-8")) > max_bytes:
        raise ValueError(
            f"RunReport remains larger than configured {max_bytes} byte limit"
        )
    return payload


def _bound_report_value(value: Any) -> Any:
    """Bound unusually large strings/lists while retaining report decisions."""
    if isinstance(value, str):
        if len(value) <= 4000:
            return value
        return value[:2000] + "\n[...truncated...]\n" + value[-1500:]
    if isinstance(value, list):
        limited = value[:100]
        converted = [_bound_report_value(item) for item in limited]
        if len(value) > len(limited):
            converted.append({"truncatedItems": len(value) - len(limited)})
        return converted
    if isinstance(value, dict):
        return {str(key): _bound_report_value(item) for key, item in value.items()}
    return value


def _convert(obj: Any) -> Any:
    """Convert report dataclasses and nested values to JSON-ready objects."""
    if isinstance(obj, CommitInfo):
        return {"sha": obj.sha, "message": obj.message, "files": obj.files}
    if isinstance(obj, ValidationSummary):
        validation_data: dict[str, Any] = {
            "commands": obj.commands,
            "passed": obj.passed,
            "outputTail": obj.output_tail,
        }
        if obj.failed_command is not None:
            validation_data["failedCommand"] = obj.failed_command
        if obj.return_code is not None:
            validation_data["returnCode"] = obj.return_code
        if obj.affected_repo is not None:
            validation_data["affectedRepo"] = obj.affected_repo
        if obj.actionable_hint is not None:
            validation_data["actionableHint"] = obj.actionable_hint
        if obj.patching_skipped is not None:
            validation_data["patchingSkipped"] = obj.patching_skipped
        return validation_data
    if isinstance(obj, PublishStatus):
        publish_data: dict[str, Any] = {"destination": obj.destination, "ok": obj.ok}
        if obj.error is not None:
            publish_data["error"] = obj.error
        if obj.fallback_path is not None:
            publish_data["fallbackPath"] = obj.fallback_path
        return publish_data
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
            "reportQuality": _report_quality(obj),
        }
        if obj.skills_baseline is not None:
            data["skillsBaseline"] = _convert(obj.skills_baseline)
        if obj.ai_usage is not None:
            data["aiUsage"] = _convert(obj.ai_usage)
        if obj.baseline_validation is not None:
            data["baselineValidation"] = _convert(obj.baseline_validation)
        if obj.error is not None:
            data["error"] = obj.error
        return data
    if isinstance(obj, dict):
        return {str(key): _convert(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_convert(item) for item in obj]
    return obj
