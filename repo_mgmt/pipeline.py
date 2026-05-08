"""
Pipeline orchestrator for the Repo Management Suite.

Provides two APIs:

1. Legacy functional API (used by tests, CLI, and API endpoint):
     report = run(pipeline_id, settings, r2, dry_run=True)
     is_running(pipeline_id) -> bool
     _pipeline_locks: dict[str, threading.Lock]

2. Class-based API (used by scheduler/api for future extensibility):
     pipeline = RmsPipeline.for_id(pipeline_id, cfg, r2, router)
     report   = asyncio.run(pipeline.run(dry_run=True))

Both return a repo_mgmt.report_writer.RunReport.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repo_mgmt import audit_reader, git_ops, issue_normaliser, validator
from repo_mgmt.model_router import ModelRouter
from repo_mgmt.patch_protocol import PathTraversalError, ProtectedPathError
from repo_mgmt.report_writer import RunReport, TaskReport, make_run_id, write
from repo_mgmt.task_ranker import rank

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId, Settings
    from repo_mgmt.r2_client import R2Client

logger = logging.getLogger(__name__)

# ── Per-pipeline concurrency locks ─────────────────────────────────────────

_pipeline_locks: dict[str, threading.Lock] = {
    "seo-aeo-geo": threading.Lock(),
    "mobile-ux":   threading.Lock(),
    "on-brand":    threading.Lock(),
}


def is_running(pipeline_id: str) -> bool:
    """Return True if the named pipeline lock is currently held."""
    lock = _pipeline_locks.get(pipeline_id)
    if lock is None:
        return False
    acquired = lock.acquire(blocking=False)
    if acquired:
        lock.release()
        return False
    return True


# ── Protected and approved sets ────────────────────────────────────────────

_PROTECTED: dict[str, frozenset[str]] = {
    "mobile-ux": frozenset([
        "blog/posts/",
        "blog/posts.json",
        "transcripts/",
        "data/podcast-episodes.json",
        "assets/js/podcast-transcripts.min.js",
        "functions/transcripts/",
    ]),
    "on-brand": frozenset(),
    "seo-aeo-geo": frozenset(),
}

_APPROVED_FIX_CLASSES: dict[str, frozenset[str]] = {
    "seo-aeo-geo": frozenset([
        "route_fix", "config_fix", "schema_fix",
        "prompt_template_update", "audit_output_fix", "middleware_fix",
    ]),
    "mobile-ux": frozenset([
        "html_fix", "css_fix", "meta_fix", "viewport_fix",
        "accessibility_fix", "redirect_fix",
    ]),
    "on-brand": frozenset([
        "html_fix", "css_fix", "template_fix", "partial_fix",
        "redirect_fix", "prompt_template_update", "schema_fix", "meta_fix",
    ]),
}


# ── Legacy synchronous functional API ─────────────────────────────────────


def run(
    pipeline_id: "PipelineId",
    settings: "Settings",
    r2: "R2Client",
    dry_run: bool = True,
) -> RunReport:
    """
    Run a single pipeline synchronously and return a RunReport.

    Concurrency: acquires pipeline lock non-blocking; if lock is already held,
    returns immediately with error='pipeline already running'.

    Args:
        pipeline_id: One of "seo-aeo-geo", "mobile-ux", "on-brand".
        settings: Validated Settings instance.
        r2: Initialised R2Client.
        dry_run: If True, no filesystem writes or git commits are made.

    Returns:
        RunReport dataclass instance.
    """
    lock = _pipeline_locks.get(pipeline_id)
    if lock is None:
        return _empty_report(pipeline_id, dry_run, error=f"Unknown pipeline: {pipeline_id!r}")

    acquired = lock.acquire(blocking=False)
    if not acquired:
        logger.warning("pipeline [%s]: already running — rejecting duplicate request", pipeline_id)
        return _empty_report(
            pipeline_id, dry_run, error="pipeline already running"
        )

    try:
        return _execute(pipeline_id, settings, r2, dry_run)
    finally:
        lock.release()


def _execute(
    pipeline_id: str,
    settings: "Settings",
    r2: "R2Client",
    dry_run: bool,
) -> RunReport:
    """Internal: run all pipeline stages and return a RunReport."""
    run_id = make_run_id()
    run_date = run_id[:10]
    started_at = datetime.now(timezone.utc).isoformat()

    logger.info(
        "pipeline [%s]: starting %s (dry_run=%s)", pipeline_id, run_id, dry_run
    )

    # 1. Read audit
    try:
        raw_audit: dict[str, Any] = audit_reader.read_latest(
            pipeline_id, r2, settings.r2_bucket_audits  # type: ignore[arg-type]
        )
    except Exception as exc:
        logger.error("pipeline [%s]: audit read failed: %s", pipeline_id, exc)
        finished_at = datetime.now(timezone.utc).isoformat()
        return RunReport(
            run_id=run_id,
            pipeline=pipeline_id,
            dry_run=dry_run,
            started_at=started_at,
            finished_at=finished_at,
            issues_total=0,
            issues_applied=0,
            issues_reverted=0,
            issues_skipped=0,
            issues_future_guidance=0,
            issues_manual_review=0,
            error=str(exc),
        )

    # 2. Normalise findings
    issues = issue_normaliser.normalise(
        audit=raw_audit,
        pipeline_id=pipeline_id,  # type: ignore[arg-type]
        run_date=run_date,
        cfg=settings,
    )

    # 3. Rank into queues
    queues = rank(issues, max_code_fix=settings.rms_max_issues_per_run)

    task_reports: list[TaskReport] = []
    issues_applied = 0
    issues_reverted = 0
    issues_skipped = 0

    # Count non-code-fix tasks
    issues_future_guidance = len(queues.future_guidance)
    issues_manual_review = len(queues.manual_review)

    # Add future_guidance and manual_review as TaskReports
    for issue in queues.future_guidance:
        task_reports.append(TaskReport(
            task_id=issue["taskId"],
            classification="future_guidance",
            status=issue.get("status", "future_guidance"),
            affected_paths=issue.get("affectedPaths", []),
        ))
    for issue in queues.manual_review:
        task_reports.append(TaskReport(
            task_id=issue["taskId"],
            classification="manual_review",
            status=issue.get("status", "manual_review"),
            affected_paths=issue.get("affectedPaths", []),
        ))

    # 4. Initialise ModelRouter for patch planning
    router = ModelRouter(settings)

    target_repo = settings.repo_path_for(pipeline_id)  # type: ignore[arg-type]
    protected = _PROTECTED.get(pipeline_id, frozenset())
    validation_commands = settings.validation_commands_for(pipeline_id)  # type: ignore[arg-type]

    # 5. Process code_fix tasks
    branch_name: str | None = None
    git_repo = None

    if queues.code_fix and not dry_run and target_repo.is_dir():
        branch_name = (
            f"{settings.rms_qa_branch_prefix}{pipeline_id}/{run_id}"
        )
        try:
            git_repo = git_ops.ensure_clean_branch(target_repo, branch_name)
        except Exception as exc:
            logger.error("pipeline [%s]: git branch error: %s", pipeline_id, exc)

    for issue in queues.code_fix:
        task_report = _execute_task(
            issue=issue,
            target_repo=target_repo,
            pipeline_id=pipeline_id,
            validation_commands=validation_commands,
            protected=protected,
            settings=settings,
            router=router,
            git_repo=git_repo,
            dry_run=dry_run,
        )
        task_reports.append(task_report)

        if task_report.status == "applied":
            issues_applied += 1
        elif task_report.status == "reverted":
            issues_reverted += 1
        else:
            issues_skipped += 1

    # 6. Publish report
    finished_at = datetime.now(timezone.utc).isoformat()
    report = RunReport(
        run_id=run_id,
        pipeline=pipeline_id,
        dry_run=dry_run,
        started_at=started_at,
        finished_at=finished_at,
        issues_total=len(issues),
        issues_applied=issues_applied,
        issues_reverted=issues_reverted,
        issues_skipped=issues_skipped,
        issues_future_guidance=issues_future_guidance,
        issues_manual_review=issues_manual_review,
        tasks=task_reports,
        validation_commands=validation_commands,
        branch=branch_name,
        error=None,
    )

    try:
        write(report, pipeline_id, settings, r2, dry_run=dry_run)  # type: ignore[arg-type]
    except Exception as exc:
        logger.error("pipeline [%s]: report write failed: %s", pipeline_id, exc)

    logger.info(
        "pipeline [%s]: finished %s — applied=%d reverted=%d",
        pipeline_id, run_id, issues_applied, issues_reverted,
    )
    return report


def _ops_to_anchor_patch(plan_dict: dict[str, Any]) -> dict[str, Any]:
    """Convert patch_planner operations dict to AnchorPatch/v1 for patch_applier."""
    changes = []
    for op in plan_dict.get("operations", []):
        action = op.get("action", "")
        if action in ("replace", "insert_after"):
            changes.append({
                "file": op.get("path", ""),
                "operation": action,
                "anchorBefore": op.get("anchorBefore", ""),
                "find": op.get("search", ""),
                "replace": op.get("replacement", ""),
                "rationale": op.get("rationale", ""),
            })
        elif action == "delete":
            changes.append({
                "file": op.get("path", ""),
                "operation": "delete",
                "anchorBefore": "",
                "find": op.get("search", ""),
                "replace": "",
                "rationale": op.get("rationale", ""),
            })
        elif action == "create":
            changes.append({
                "file": op.get("path", ""),
                "operation": "replace",
                "anchorBefore": "",
                "find": "",
                "replace": op.get("content", ""),
                "rationale": op.get("rationale", ""),
            })
    return {"patchProtocol": "AnchorPatch/v1", "changes": changes}


def _execute_task(
    *,
    issue: dict[str, Any],
    target_repo: Path,
    pipeline_id: str,
    validation_commands: list[str],
    protected: frozenset[str],
    settings: "Settings",
    router: ModelRouter,
    git_repo: Any,
    dry_run: bool,
) -> TaskReport:
    """Execute one code_fix task and return a TaskReport."""
    from repo_mgmt import patch_applier, patch_planner

    task_id = issue.get("taskId", "<unknown>")
    affected_paths: list[str] = issue.get("affectedPaths", [])

    try:
        plan_dict = patch_planner.plan(
            issue=issue,
            target_repo=target_repo,
            pipeline_id=pipeline_id,
            settings=settings,
            model_router=router,
        )
        patch_doc = _ops_to_anchor_patch(plan_dict)

        if not patch_doc.get("changes"):
            return TaskReport(
                task_id=task_id,
                classification="code_fix",
                status="skipped",
                affected_paths=affected_paths,
            )

        if dry_run:
            return TaskReport(
                task_id=task_id,
                classification="code_fix",
                status="skipped",
                affected_paths=affected_paths,
                patch_plan_ops=len(patch_doc.get("changes", [])),
            )

        modified = patch_applier.apply(
            patch_doc=patch_doc,
            target_repo=target_repo,
            dry_run=False,
            pipeline_id=pipeline_id,
        )

        val_result = validator.run(
            pipeline_id=pipeline_id,  # type: ignore[arg-type]
            repo_root=target_repo,
            cfg=settings,
            dry_run=False,
        )

        if not val_result.passed:
            if settings.rms_revert_on_validation_failure and git_repo is not None:
                git_ops.revert_to_head(git_repo, dry_run=False)
            return TaskReport(
                task_id=task_id,
                classification="code_fix",
                status="reverted" if settings.rms_revert_on_validation_failure else "validation_failed",
                affected_paths=affected_paths,
                patch_plan_ops=len(patch_doc.get("changes", [])),
                validation_passed=False,
                error=f"validation failed: {val_result.failed_command}",
            )

        sha: str | None = None
        if git_repo is not None:
            sha = git_ops.stage_and_commit(
                git_repo,
                modified,
                f"rms({pipeline_id}): {task_id} — {issue.get('title', 'fix')}",
                dry_run=False,
            )

        return TaskReport(
            task_id=task_id,
            classification="code_fix",
            status="applied",
            affected_paths=affected_paths,
            patch_plan_ops=len(patch_doc.get("changes", [])),
            validation_passed=True,
            commit_sha=sha,
        )

    except (PathTraversalError, ProtectedPathError) as exc:
        logger.error("pipeline [%s]: safety gate: %s", pipeline_id, exc)
        return TaskReport(
            task_id=task_id,
            classification="code_fix",
            status="skipped",
            affected_paths=affected_paths,
            error=str(exc),
        )
    except Exception as exc:
        logger.exception("pipeline [%s]: task %s failed", pipeline_id, task_id)
        return TaskReport(
            task_id=task_id,
            classification="code_fix",
            status="skipped",
            affected_paths=affected_paths,
            error=str(exc),
        )


def _empty_report(pipeline_id: str, dry_run: bool, error: str | None = None) -> RunReport:
    """Return a zero-count RunReport with an optional error message."""
    now = datetime.now(timezone.utc).isoformat()
    return RunReport(
        run_id=make_run_id(),
        pipeline=pipeline_id,
        dry_run=dry_run,
        started_at=now,
        finished_at=now,
        issues_total=0,
        issues_applied=0,
        issues_reverted=0,
        issues_skipped=0,
        issues_future_guidance=0,
        issues_manual_review=0,
        error=error,
    )


# ── Class-based API (for scheduler / api layer) ────────────────────────────


class RmsPipeline:
    """Thin wrapper for the functional API used by the scheduler and API layer."""

    def __init__(
        self,
        pipeline_id: "PipelineId",
        cfg: "Settings",
        r2: "R2Client",
        model_router: Any,
    ) -> None:
        self.pipeline_id = pipeline_id
        self._cfg = cfg
        self._r2 = r2
        self._model_router = model_router

    @classmethod
    def for_id(
        cls,
        pipeline_id: "PipelineId",
        cfg: "Settings",
        r2: "R2Client",
        model_router: Any,
    ) -> "RmsPipeline":
        return cls(pipeline_id, cfg, r2, model_router)

    async def run(self, dry_run: bool, run_id: str | None = None) -> RunReport:
        """Async wrapper — delegates to the synchronous functional run()."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: run(self.pipeline_id, self._cfg, self._r2, dry_run),
        )
