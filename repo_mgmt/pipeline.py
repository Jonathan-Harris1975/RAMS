"""
Pipeline orchestrator for the Repo Management Suite.

Runs the full audit → normalise → plan → apply → validate → report cycle
for a single pipeline. Each pipeline is independent and stateless.

The run() function is the single entry-point for both the API endpoints
and the CLI. It returns a RunReport and raises no exceptions — all errors
are captured in the report's error field.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from repo_mgmt import audit_reader, issue_normaliser, patch_planner, patch_applier
from repo_mgmt import git_ops, validator, report_writer
from repo_mgmt.report_writer import RunReport, TaskReport, make_run_id
from repo_mgmt.model_router import ModelRouter

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId, Settings
    from repo_mgmt.r2_client import R2Client

logger = logging.getLogger(__name__)

# Per-pipeline lock to prevent concurrent runs of the same pipeline
_pipeline_locks: dict[str, threading.Lock] = {
    "seo-aeo-geo": threading.Lock(),
    "mobile-ux": threading.Lock(),
    "on-brand": threading.Lock(),
}


def is_running(pipeline_id: "PipelineId") -> bool:
    """Return True if *pipeline_id* is currently being executed."""
    lock = _pipeline_locks[pipeline_id]
    acquired = lock.acquire(blocking=False)
    if acquired:
        lock.release()
        return False
    return True


def run(
    pipeline_id: "PipelineId",
    cfg: "Settings",
    r2: "R2Client",
    dry_run: bool | None = None,
) -> RunReport:
    """
    Execute the full pipeline for *pipeline_id*.

    Args:
        pipeline_id: One of "seo-aeo-geo", "mobile-ux", "on-brand".
        cfg: Validated RMS settings.
        r2: Initialised R2Client.
        dry_run: If provided, overrides cfg.rms_dry_run for this run.

    Returns:
        Completed RunReport (never raises).
    """
    effective_dry_run = dry_run if dry_run is not None else cfg.rms_dry_run
    run_id = make_run_id()
    started_at = datetime.now(timezone.utc).isoformat()

    lock = _pipeline_locks[pipeline_id]
    if not lock.acquire(blocking=False):
        logger.warning("pipeline [%s]: already running — returning 409 report", pipeline_id)
        return _error_report(
            run_id=run_id,
            pipeline=pipeline_id,
            dry_run=effective_dry_run,
            started_at=started_at,
            error="pipeline already running",
        )

    try:
        return _execute(
            pipeline_id=pipeline_id,
            cfg=cfg,
            r2=r2,
            dry_run=effective_dry_run,
            run_id=run_id,
            started_at=started_at,
        )
    finally:
        lock.release()


def _execute(
    pipeline_id: "PipelineId",
    cfg: "Settings",
    r2: "R2Client",
    dry_run: bool,
    run_id: str,
    started_at: str,
) -> RunReport:
    """Internal: run the pipeline with the lock already held."""

    router = ModelRouter(cfg)
    repo_root: Path = cfg.repo_path_for(pipeline_id)
    run_date = run_id[:10]  # YYYY-MM-DD

    task_reports: list[TaskReport] = []
    counts = {
        "applied": 0,
        "reverted": 0,
        "skipped": 0,
        "future_guidance": 0,
        "manual_review": 0,
    }

    branch_name = f"{cfg.rms_qa_branch_prefix}{pipeline_id}/{run_date}"
    repo = None

    try:
        # ── 1. Read latest audit ──────────────────────────────────────────
        audit = audit_reader.read_latest(pipeline_id, r2, cfg.r2_bucket_audits)

        # ── 2. Normalise issues ────────────────────────────────────────────
        issues = issue_normaliser.normalise(
            audit=audit,
            pipeline_id=pipeline_id,
            run_date=run_date,
            cfg=cfg,
            router=router,
        )

        # ── 3. Ensure feature branch ───────────────────────────────────────
        if not dry_run:
            repo = git_ops.ensure_clean_branch(repo_root, branch_name)

        # ── 4. Process up to max_issues_per_run code_fix issues ─────────────
        code_fix_count = 0
        for issue in issues:
            classification = issue["classification"]
            task_id = issue["taskId"]

            # Collect non-code_fix immediately
            if classification != "code_fix":
                _count_and_append(
                    task_reports,
                    counts,
                    TaskReport(
                        task_id=task_id,
                        classification=classification,
                        status=issue["status"],
                        affected_paths=issue["affectedPaths"],
                    ),
                )
                continue

            if code_fix_count >= cfg.rms_max_issues_per_run:
                logger.info(
                    "pipeline [%s]: max_issues_per_run=%d reached — skipping %s",
                    pipeline_id,
                    cfg.rms_max_issues_per_run,
                    task_id,
                )
                _count_and_append(
                    task_reports,
                    counts,
                    TaskReport(
                        task_id=task_id,
                        classification=classification,
                        status="skipped_limit_reached",
                        affected_paths=issue["affectedPaths"],
                    ),
                )
                counts["skipped"] += 1
                continue

            code_fix_count += 1
            task_rep = _process_code_fix(
                issue=issue,
                pipeline_id=pipeline_id,
                repo_root=repo_root,
                repo=repo,
                cfg=cfg,
                router=router,
                dry_run=dry_run,
                branch_name=branch_name,
            )
            task_reports.append(task_rep)
            if task_rep.status == "applied":
                counts["applied"] += 1
            elif task_rep.status == "reverted":
                counts["reverted"] += 1
            else:
                counts["skipped"] += 1

        # ── 5. Push branch (if enabled) ────────────────────────────────────
        if repo is not None:
            git_ops.push_branch(
                repo,
                branch_name,
                dry_run=dry_run,
                push_enabled=cfg.rms_push_enabled,
            )

    except Exception as exc:
        logger.exception("pipeline [%s]: unexpected error: %s", pipeline_id, exc)
        report = _error_report(
            run_id=run_id,
            pipeline=pipeline_id,
            dry_run=dry_run,
            started_at=started_at,
            error=str(exc),
        )
        report.tasks = task_reports
        _publish(report, pipeline_id, cfg, r2, dry_run)
        return report

    # ── 6. Build and publish report ────────────────────────────────────────
    finished_at = datetime.now(timezone.utc).isoformat()
    report = RunReport(
        run_id=run_id,
        pipeline=pipeline_id,
        dry_run=dry_run,
        started_at=started_at,
        finished_at=finished_at,
        issues_total=len(issues) if "issues" in dir() else 0,
        issues_applied=counts["applied"],
        issues_reverted=counts["reverted"],
        issues_skipped=counts["skipped"],
        issues_future_guidance=counts["future_guidance"],
        issues_manual_review=counts["manual_review"],
        tasks=task_reports,
        validation_commands=cfg.validation_commands_for(pipeline_id),
        branch=branch_name if not dry_run else None,
    )
    _publish(report, pipeline_id, cfg, r2, dry_run)
    return report


def _process_code_fix(
    issue: dict,
    pipeline_id: "PipelineId",
    repo_root: Path,
    repo,
    cfg: "Settings",
    router: "ModelRouter",
    dry_run: bool,
    branch_name: str,
) -> TaskReport:
    """Plan, apply, validate, and commit a single code_fix issue."""
    task_id = issue["taskId"]

    # Plan
    try:
        plan = patch_planner.plan(
            issue=issue,
            repo_root=repo_root,
            pipeline_id=pipeline_id,
            cfg=cfg,
            router=router,
        )
    except patch_planner.PatchPlanError as exc:
        logger.error("pipeline [%s]: planning failed for %s: %s", pipeline_id, task_id, exc)
        return TaskReport(
            task_id=task_id,
            classification="code_fix",
            status="skipped_plan_error",
            affected_paths=issue["affectedPaths"],
            error=str(exc),
        )

    # Apply
    try:
        modified_paths = patch_applier.apply(
            patch_plan=plan,
            repo_root=repo_root,
            dry_run=dry_run,
        )
    except patch_applier.PatchApplyError as exc:
        logger.error("pipeline [%s]: apply failed for %s: %s", pipeline_id, task_id, exc)
        return TaskReport(
            task_id=task_id,
            classification="code_fix",
            status="skipped_apply_error",
            affected_paths=issue["affectedPaths"],
            patch_plan_ops=len(plan.get("operations", [])),
            error=str(exc),
        )

    # Validate
    if cfg.rms_validate_after_each_task:
        val_result = validator.run(pipeline_id, repo_root, cfg, dry_run=dry_run)
        if not val_result.passed:
            logger.warning(
                "pipeline [%s]: validation FAILED for %s — reverting", pipeline_id, task_id
            )
            if cfg.rms_revert_on_validation_failure and repo is not None:
                try:
                    git_ops.revert_to_head(repo, dry_run=dry_run)
                except git_ops.GitOpsError as exc:
                    logger.error("pipeline [%s]: revert failed: %s", pipeline_id, exc)
            return TaskReport(
                task_id=task_id,
                classification="code_fix",
                status="reverted",
                affected_paths=issue["affectedPaths"],
                patch_plan_ops=len(plan.get("operations", [])),
                validation_passed=False,
            )
    else:
        val_result = None

    # Commit
    commit_sha: str | None = None
    if repo is not None and modified_paths:
        try:
            commit_sha = git_ops.stage_and_commit(
                repo=repo,
                paths=modified_paths,
                message=f"rms({pipeline_id}): {task_id} — {issue.get('requiredOutcome', '')[:72]}",
                dry_run=dry_run,
            )
        except (git_ops.BranchSafetyError, git_ops.GitOpsError) as exc:
            logger.error("pipeline [%s]: commit failed for %s: %s", pipeline_id, task_id, exc)

    return TaskReport(
        task_id=task_id,
        classification="code_fix",
        status="applied",
        affected_paths=issue["affectedPaths"],
        patch_plan_ops=len(plan.get("operations", [])),
        validation_passed=val_result.passed if val_result else None,
        commit_sha=commit_sha,
    )


def _count_and_append(
    task_reports: list[TaskReport],
    counts: dict[str, int],
    task_rep: TaskReport,
) -> None:
    task_reports.append(task_rep)
    if task_rep.status == "future_guidance":
        counts["future_guidance"] += 1
    elif task_rep.status == "manual_review":
        counts["manual_review"] += 1
    else:
        counts["skipped"] += 1


def _error_report(
    run_id: str,
    pipeline: str,
    dry_run: bool,
    started_at: str,
    error: str,
) -> RunReport:
    return RunReport(
        run_id=run_id,
        pipeline=pipeline,
        dry_run=dry_run,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
        issues_total=0,
        issues_applied=0,
        issues_reverted=0,
        issues_skipped=0,
        issues_future_guidance=0,
        issues_manual_review=0,
        error=error,
    )


def _publish(
    report: RunReport,
    pipeline_id: "PipelineId",
    cfg: "Settings",
    r2: "R2Client",
    dry_run: bool,
) -> None:
    try:
        report_writer.write(report, pipeline_id, cfg, r2, dry_run=dry_run)
    except Exception as exc:
        logger.error(
            "pipeline [%s]: failed to publish report: %s", pipeline_id, exc
        )
