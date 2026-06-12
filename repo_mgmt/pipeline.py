"""Active RAMS pipeline orchestration."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_mgmt import (
    audit_reader,
    issue_normaliser,
    task_ranker,
    update_executor,
    validation_runner,
)
from repo_mgmt.config import PipelineId, Settings, configured_worker_count
from repo_mgmt.git_manager import GitManager
from repo_mgmt.lane1_skills import build_lane1_skills_baseline
from repo_mgmt.model_router import ModelRouter
from repo_mgmt.report_publisher import (
    CommitInfo,
    RunReport,
    ValidationSummary,
    make_run_id,
    publish,
    write_local_fallback,
)

logger = logging.getLogger(__name__)

_PIPELINE_IDS = ("seo-aeo-geo", "mobile-ux", "on-brand")
_pipeline_locks = {pipeline: threading.Lock() for pipeline in _PIPELINE_IDS}
_global_pipeline_lock = threading.Lock()


def is_running(pipeline_id: PipelineId) -> bool:
    """Return true when this pipeline or RAMS's global eMicro lock is held."""
    return _pipeline_locks[pipeline_id].locked() or _global_pipeline_lock.locked()


def _date() -> str:
    """Return the UTC date string used in deterministic task IDs."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def _summary(tasks: list[dict[str, Any]], snapshots: int) -> dict[str, int]:
    """Build a RunReport summary from explicit task state markers."""
    code_fix_candidates = sum(
        1 for task in tasks if task.get("classification") == "code_fix"
    )
    patch_applied = sum(1 for task in tasks if bool(task.get("patchApplied")))
    commit_created = sum(1 for task in tasks if bool(task.get("commit_sha")))
    manual_review = sum(
        1
        for task in tasks
        if task.get("classification") == "manual_review"
        or task.get("status") == "manual_review"
    )
    return {
        "snapshotsRead": snapshots,
        "tasksGenerated": len(tasks),
        "codeFixCandidates": code_fix_candidates,
        "codeFixesAttempted": sum(
            1 for task in tasks if bool(task.get("patchAttempted"))
        ),
        "baselineValidationFailed": sum(
            1 for task in tasks if bool(task.get("baselineValidationFailed"))
        ),
        "patchApplied": patch_applied,
        "validationFailed": sum(
            1 for task in tasks if bool(task.get("validationFailed"))
        ),
        "commitCreated": commit_created,
        "manualReview": manual_review,
        "skipped": sum(
            1
            for task in tasks
            if task.get("classification") == "skipped"
            or str(task.get("status", "")).startswith("skipped")
        ),
        "unsafeWriteRefused": sum(
            1 for task in tasks if bool(task.get("unsafeWriteRefused"))
        ),
        # Backwards-compatible keys retained for older dashboards/tests.
        "committed": commit_created,
        "futureGuidance": sum(
            1
            for task in tasks
            if task.get("classification") == "future_guidance"
            or task.get("status") == "future_guidance"
        ),
        "manualReviewLegacy": manual_review,
    }


def _validation_summary(
    tasks: list[dict[str, Any]], cfg: Settings, pipeline_id: PipelineId
) -> ValidationSummary:
    """Return the latest validation summary or an explicit not-run object."""
    for task in reversed(tasks):
        validation = task.get("validation")
        if validation:
            return ValidationSummary(
                commands=validation.get(
                    "commands", cfg.validation_commands_for(pipeline_id)
                ),
                passed=bool(validation.get("passed")),
                output_tail=str(validation.get("outputTail", "")),
                failed_command=validation.get("failedCommand"),
                return_code=validation.get("returnCode"),
                affected_repo=validation.get("affectedRepo"),
                actionable_hint=validation.get("actionableHint"),
                patching_skipped=validation.get("patchingSkipped"),
            )
    return ValidationSummary(
        commands=cfg.validation_commands_for(pipeline_id),
        passed=False,
        output_tail="not_run: validation did not run for this pipeline run",
    )


def _commits(tasks: list[dict[str, Any]]) -> list[CommitInfo]:
    """Extract commit metadata from completed tasks."""
    return [
        CommitInfo(
            sha=str(task["commit_sha"]),
            message=str(task.get("commit_message", "")),
            files=list(task.get("modified_files", [])),
        )
        for task in tasks
        if task.get("commit_sha")
    ]


def _mark_code_fixes_manual(
    issues: list[dict[str, Any]],
    reason: str,
    baseline_validation: ValidationSummary | None = None,
) -> list[dict[str, Any]]:
    """Return selected issues with code fixes converted to manual review."""
    tasks: list[dict[str, Any]] = []
    for issue in issues:
        task = dict(issue)
        if task.get("classification") == "code_fix":
            task["status"] = "manual_review"
            task["error"] = reason
            task["patchAttempted"] = False
            task["patchApplied"] = False
            task["baselineValidationFailed"] = (
                baseline_validation is not None and not baseline_validation.passed
            )
            task["patchingSkippedBecauseBaselineFailed"] = task[
                "baselineValidationFailed"
            ]
            task["evidence"] = list(task.get("evidence", [])) + [reason]
            if baseline_validation is not None:
                task["baselineValidation"] = _validation_to_task_block(
                    baseline_validation
                )
        tasks.append(task)
    return tasks


def _validation_to_task_block(validation: ValidationSummary) -> dict[str, Any]:
    """Return the task-level baseline validation block for report consumers."""
    block: dict[str, Any] = {
        "commands": validation.commands,
        "passed": validation.passed,
        "outputTail": validation.output_tail,
    }
    if validation.failed_command is not None:
        block["failedCommand"] = validation.failed_command
    if validation.return_code is not None:
        block["returnCode"] = validation.return_code
    if validation.affected_repo is not None:
        block["affectedRepo"] = validation.affected_repo
    if validation.actionable_hint is not None:
        block["actionableHint"] = validation.actionable_hint
    if validation.patching_skipped is not None:
        block["patchingSkipped"] = validation.patching_skipped
    return block


def _baseline_actionable_hint(result: validation_runner.ValidationResult) -> str:
    """Return a concise operator hint for a failed clean-repo validation."""
    command = result.failed_command or "validation command"
    output = result.output_tail
    combined = f"{command}\n{output}"
    if "scripts/ebook_pipeline.py" in combined or "sync_redirects.py" in combined:
        return (
            "Fix scripts/ebook_pipeline.py syntax error, then rerun "
            "python3 scripts/sync_redirects.py --check before attempting RAMS live patching."
        )
    return f"Fix the clean-repo validation failure, then rerun {command} before attempting RAMS live patching."


def _make_report(
    *,
    run_id: str,
    pipeline_id: PipelineId,
    target_repo: Path,
    branch: str,
    dry_run: bool,
    tasks: list[dict[str, Any]],
    snapshots: int,
    cfg: Settings,
    error: str | None,
    baseline_validation: ValidationSummary | None = None,
    ai_usage: dict[str, Any] | None = None,
) -> RunReport:
    """Construct a RunReport from current pipeline state."""
    return RunReport(
        runId=run_id,
        pipeline=pipeline_id,
        targetRepo=str(target_repo),
        branch=branch,
        dryRun=dry_run,
        summary=_summary(tasks, snapshots),
        tasks=tasks,
        validation=_validation_summary(tasks, cfg, pipeline_id),
        baseline_validation=baseline_validation,
        commits=_commits(tasks),
        error=error,
        skills_baseline=build_lane1_skills_baseline(pipeline_id=pipeline_id),
        ai_usage=ai_usage,
    )


def _publish_report(report: RunReport, cfg: Settings, r2: Any) -> None:
    """Publish a report and always leave a local fallback on publish failure."""
    try:
        publish(report, cfg, r2)
    except Exception as exc:
        logger.exception(
            "pipeline: failed to publish report pipeline=%s runId=%s",
            report.pipeline,
            report.runId,
        )
        reason = str(exc)
        report.error = (report.error + "; " if report.error else "") + (
            f"report publish failed: {reason}"
        )
        try:
            write_local_fallback(report, cfg, reason)
        except Exception:
            logger.exception(
                "pipeline: failed to write local fallback report pipeline=%s runId=%s",
                report.pipeline,
                report.runId,
            )


def _preflight_live_repo(target_repo: Path, cfg: Settings) -> None:
    """Fail closed if deployment or repo state is unsafe for live mutation."""
    if cfg.rms_single_worker_mode and configured_worker_count() != 1:
        raise RuntimeError(
            "live mode requires a single worker because RAMS uses in-process locks"
        )
    if not cfg.live_write_permitted:
        raise RuntimeError(
            "live mode is not permitted; require RMS_DRY_RUN=false and "
            "RMS_LIVE_WRITE_ENABLED=true"
        )
    if not target_repo.exists() or not target_repo.is_dir():
        raise RuntimeError(f"target repo path is missing or invalid: {target_repo}")
    git_mgr = GitManager(
        target_repo,
        cfg.rms_qa_branch_prefix,
        cfg.rms_push_enabled,
        cfg.rms_git_timeout_seconds,
        cfg.rms_git_output_max_bytes,
    )
    if not git_mgr.is_git_repo():
        raise RuntimeError(f"target repo is not a Git worktree: {target_repo}")
    git_mgr.assert_clean_worktree()


def _run_baseline_validation(
    pipeline_id: PipelineId, target_repo: Path, cfg: Settings
) -> ValidationSummary:
    """Validate the clean cloned repo before applying any live patch."""
    result = validation_runner.run(pipeline_id, target_repo, cfg, dry_run=False)
    summary = ValidationSummary(
        commands=result.commands,
        passed=result.passed,
        output_tail=result.output_tail,
        failed_command=result.failed_command,
        return_code=result.return_code,
        affected_repo=str(target_repo),
        actionable_hint=None if result.passed else _baseline_actionable_hint(result),
        patching_skipped=None if result.passed else True,
    )
    if not result.passed:
        logger.warning(
            "pipeline: baseline validation failed pipeline=%s command=%s returnCode=%s outputTail=%s",
            pipeline_id,
            result.failed_command or "<unknown>",
            result.return_code,
            result.output_tail[-2000:],
        )
    return summary


class RmsPipeline:
    """One independent RMS audit pipeline."""

    def __init__(
        self,
        pipeline_id: PipelineId,
        cfg: Settings,
        r2: Any,
        router: ModelRouter | None = None,
    ) -> None:
        """Initialise a pipeline with its settings, R2 client, and model router."""
        self.pipeline_id = pipeline_id
        self.cfg = cfg
        self.r2 = r2
        self.router = router or ModelRouter(cfg)

    @classmethod
    def for_id(
        cls,
        pipeline_id: PipelineId,
        cfg: Settings,
        r2: Any,
        router: ModelRouter | None = None,
    ) -> "RmsPipeline":
        """Construct a pipeline for *pipeline_id*."""
        return cls(pipeline_id, cfg, r2, router)

    async def run(
        self,
        dry_run: bool | None = None,
        run_id: str | None = None,
    ) -> RunReport:
        """Run this pipeline and return its report."""
        return await _run_async(
            self.pipeline_id,
            self.cfg,
            self.r2,
            self.router,
            self.cfg.rms_dry_run if dry_run is None else dry_run,
            run_id=run_id,
        )

    @property
    def audit_key(self) -> str:
        """R2 key for this pipeline's latest audit snapshot."""
        return f"audits/{self.pipeline_id}/latest.json"

    @property
    def target_repo(self) -> Path:
        """Absolute path to the target repository clone."""
        return self.cfg.repo_path_for(self.pipeline_id)

    @property
    def validation_commands(self) -> list[str]:
        """Ordered validation commands for this pipeline."""
        return self.cfg.validation_commands_for(self.pipeline_id)

    @property
    def protected_paths(self) -> frozenset[str]:
        """Repo-relative path prefixes that this pipeline may not modify."""
        from repo_mgmt.patch_applier import PROTECTED_PATHS

        return PROTECTED_PATHS.get(self.pipeline_id, frozenset())

    @property
    def approved_fix_classes(self) -> frozenset[str]:
        """Fix classes this pipeline is permitted to apply."""
        approved: dict[str, frozenset[str]] = {
            "seo-aeo-geo": frozenset(
                {
                    "html_fix",
                    "css_fix",
                    "meta_fix",
                    "schema_fix",
                    "structured_data_fix",
                    "canonical_fix",
                    "redirect_fix",
                    "crawler_fix",
                    "sitemap_fix",
                    "robots_fix",
                    "llms_fix",
                    "accessibility_fix",
                    "template_fix",
                    "partial_fix",
                }
            ),
            "mobile-ux": frozenset(
                {
                    "html_fix",
                    "css_fix",
                    "meta_fix",
                    "viewport_fix",
                    "accessibility_fix",
                    "redirect_fix",
                }
            ),
            "on-brand": frozenset(
                {
                    "route_fix",
                    "config_fix",
                    "schema_fix",
                    "prompt_template_update",
                    "audit_output_fix",
                    "middleware_fix",
                    "html_fix",
                    "css_fix",
                    "template_fix",
                    "partial_fix",
                    "redirect_fix",
                    "meta_fix",
                }
            ),
        }
        return approved.get(self.pipeline_id, frozenset())


def _start_router_run(router: Any, run_id: str) -> None:
    """Reset usage metrics when supported by the configured router."""
    method = getattr(router, "start_run", None)
    if callable(method):
        method(run_id)


def _router_usage(router: Any) -> dict[str, Any] | None:
    """Return router usage only when it is a real JSON dictionary."""
    method = getattr(router, "usage_summary", None)
    if not callable(method):
        return None
    result = method()
    return result if isinstance(result, dict) else None


async def _run_async(
    pipeline_id: PipelineId,
    cfg: Settings,
    r2: Any,
    router: ModelRouter,
    dry_run: bool,
    *,
    run_id: str | None = None,
) -> RunReport:
    """Execute a pipeline with a single source-of-truth run ID."""
    actual_run_id = run_id or make_run_id()
    target_repo = cfg.repo_path_for(pipeline_id)
    branch = f"{cfg.rms_qa_branch_prefix}{pipeline_id}/{actual_run_id}"
    tasks: list[dict[str, Any]] = []
    error: str | None = None
    baseline_validation: ValidationSummary | None = None
    snapshots = 0
    lock = _pipeline_locks[pipeline_id]

    if not lock.acquire(False):
        return _make_report(
            run_id=actual_run_id,
            pipeline_id=pipeline_id,
            target_repo=target_repo,
            branch=branch,
            dry_run=dry_run,
            tasks=[],
            snapshots=0,
            cfg=cfg,
            error=f"pipeline {pipeline_id!r} already running",
            baseline_validation=None,
        )

    if not _global_pipeline_lock.acquire(False):
        lock.release()
        return _make_report(
            run_id=actual_run_id,
            pipeline_id=pipeline_id,
            target_repo=target_repo,
            branch=branch,
            dry_run=dry_run,
            tasks=[],
            snapshots=0,
            cfg=cfg,
            error="another RAMS pipeline is already running on this eMicro instance",
            baseline_validation=None,
        )

    _start_router_run(router, actual_run_id)
    try:
        audit = await asyncio.to_thread(
            audit_reader.read_latest,
            pipeline_id,
            r2,
            cfg.r2_bucket_audits,
            max_artefacts=cfg.rms_max_audit_artefacts,
            max_object_bytes=cfg.rms_max_audit_object_bytes,
            max_total_bytes=cfg.rms_max_audit_total_bytes,
        )
        snapshots = 1 if audit else 0
        issues = await asyncio.to_thread(
            issue_normaliser.normalise, audit, pipeline_id, _date(), cfg, router
        )
        queues = task_ranker.rank(issues, cfg.rms_max_issues_per_run)
        selected = [*queues.code_fix, *queues.manual_review, *queues.future_guidance]

        git_mgr = None
        if not dry_run and queues.code_fix:
            try:
                await asyncio.to_thread(_preflight_live_repo, target_repo, cfg)
                baseline_validation = await asyncio.to_thread(
                    _run_baseline_validation, pipeline_id, target_repo, cfg
                )
                if not baseline_validation.passed:
                    failed_command = baseline_validation.output_tail.splitlines()[-1:]
                    detail = (
                        failed_command[0] if failed_command else "validation failed"
                    )
                    raise RuntimeError(
                        "baseline validation failed before patch; "
                        f"live code_fix writes skipped: {detail}"
                    )
                git_mgr = GitManager(
                    target_repo,
                    cfg.rms_qa_branch_prefix,
                    cfg.rms_push_enabled,
                    cfg.rms_git_timeout_seconds,
                    cfg.rms_git_output_max_bytes,
                )
                await asyncio.to_thread(git_mgr.create_branch, branch)
            except Exception as exc:
                error = (
                    f"Git/live preflight failed; live code_fix writes skipped: {exc}"
                )
                tasks = _mark_code_fixes_manual(selected, error, baseline_validation)
                selected = []

        for issue in selected:
            if issue.get("classification") == "code_fix":
                tasks.append(
                    await update_executor.run_task(
                        issue,
                        target_repo,
                        pipeline_id,
                        cfg,
                        router,
                        git_mgr,
                        dry_run,
                    )
                )
            else:
                tasks.append(dict(issue))

        report = _make_report(
            run_id=actual_run_id,
            pipeline_id=pipeline_id,
            target_repo=target_repo,
            branch=branch,
            dry_run=dry_run,
            tasks=tasks,
            snapshots=snapshots,
            cfg=cfg,
            error=error,
            baseline_validation=baseline_validation,
            ai_usage=_router_usage(router),
        )
        await asyncio.to_thread(_publish_report, report, cfg, r2)
        return report
    except Exception as exc:
        report = _make_report(
            run_id=actual_run_id,
            pipeline_id=pipeline_id,
            target_repo=target_repo,
            branch=branch,
            dry_run=dry_run,
            tasks=tasks,
            snapshots=snapshots,
            cfg=cfg,
            error=str(exc),
            baseline_validation=baseline_validation,
            ai_usage=_router_usage(router),
        )
        await asyncio.to_thread(_publish_report, report, cfg, r2)
        return report
    finally:
        _global_pipeline_lock.release()
        lock.release()


def run(
    pipeline_id: PipelineId,
    cfg: Settings,
    r2: Any,
    dry_run: bool | None = None,
    run_id: str | None = None,
) -> RunReport:
    """Synchronously run a pipeline for CLI/tests outside an async context."""
    return asyncio.run(
        _run_async(
            pipeline_id,
            cfg,
            r2,
            ModelRouter(cfg),
            cfg.rms_dry_run if dry_run is None else dry_run,
            run_id=run_id,
        )
    )
