"""Active RAMS pipeline orchestration."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_mgmt import (
    audit_reader,
    github_pr,
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
    PullRequestInfo,
    RunReport,
    ValidationSummary,
    make_run_id,
    publish,
    write_local_fallback,
)

logger = logging.getLogger(__name__)

_PIPELINE_IDS = ("website", "seo-aeo-geo", "mobile-ux", "on-brand")
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
        "commitsPushed": sum(1 for task in tasks if bool(task.get("pushed"))),
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
    pull_request: PullRequestInfo | None = None,
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
        pull_request=pull_request,
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
        cfg.github_token_value,
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
        audit_json_key: str | None = None,
    ) -> RunReport:
        """Run this pipeline and return its report.

        The unified ``website`` pipeline consumes the exact final JSON report
        key supplied by AIMS. Legacy pipelines continue to use their latest
        pointers for backwards compatibility.
        """
        return await _run_async(
            self.pipeline_id,
            self.cfg,
            self.r2,
            self.router,
            self.cfg.rms_dry_run if dry_run is None else dry_run,
            run_id=run_id,
            audit_json_key=audit_json_key,
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
            "website": frozenset(
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
                    "internal_link_fix",
                    "viewport_fix",
                }
            ),
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


def _append_error(current: str | None, addition: str) -> str:
    """Append one bounded operational error without losing earlier context."""
    return f"{current}; {addition}" if current else addition


def _pull_request_body(
    *,
    pipeline_id: PipelineId,
    run_id: str,
    audit_json_key: str | None,
    tasks: list[dict[str, Any]],
) -> str:
    """Build a concise, non-secret body for an automatically created RAMS PR."""
    committed = [task for task in tasks if task.get("commit_sha") and task.get("pushed")]
    lines = [
        "## RAMS automated remediation",
        "",
        f"- Pipeline: `{pipeline_id}`",
        f"- Run: `{run_id}`",
        f"- Validated commits: **{len(committed)}**",
    ]
    if audit_json_key:
        lines.append(f"- Audit source: `{audit_json_key}`")
    lines.extend(["", "### Included fixes"])
    for task in committed[:25]:
        task_id = str(task.get("taskId") or "unknown")
        title = str(task.get("title") or task.get("requiredOutcome") or "validated fix")
        severity = str(task.get("severity") or "unknown")
        lines.append(f"- `{task_id}` [{severity}] {title}")
    if len(committed) > 25:
        lines.append(f"- …plus {len(committed) - 25} additional validated commits")
    lines.extend(
        [
            "",
            "Every included change passed RAMS repository safety checks, the Phase 4C autonomous engineering gate and configured post-patch validation before publication.",
            "",
            "This pull request was created automatically by RAMS. It is not automatically merged.",
        ]
    )
    return "\n".join(lines)


def _create_automatic_pr(
    *,
    pipeline_id: PipelineId,
    run_id: str,
    branch: str,
    audit_json_key: str | None,
    tasks: list[dict[str, Any]],
    cfg: Settings,
) -> PullRequestInfo | None:
    """Create or resolve the single automatic PR for pushed commits in this run."""
    if not cfg.rms_create_pr:
        return None
    pushed_commits = [task for task in tasks if task.get("commit_sha") and task.get("pushed")]
    if not pushed_commits:
        return None
    result = github_pr.create_or_get_pull_request(
        token=cfg.github_token_value,
        repo_url=cfg.repo_url_for(pipeline_id),
        base_branch=cfg.repo_branch_for(pipeline_id),
        head_branch=branch,
        title=f"RAMS {pipeline_id} remediation · {run_id}",
        body=_pull_request_body(
            pipeline_id=pipeline_id,
            run_id=run_id,
            audit_json_key=audit_json_key,
            tasks=tasks,
        ),
        api_base=cfg.rms_github_api_base,
        timeout_seconds=cfg.rms_github_api_timeout_seconds,
        max_retries=cfg.rms_github_api_max_retries,
    )
    for task in pushed_commits:
        task["pullRequestNumber"] = result.number
        task["pullRequestUrl"] = result.url
    return PullRequestInfo(
        number=result.number,
        url=result.url,
        title=result.title,
        base=result.base,
        head=result.head,
        created=result.created,
    )


async def _run_async(
    pipeline_id: PipelineId,
    cfg: Settings,
    r2: Any,
    router: ModelRouter,
    dry_run: bool,
    *,
    run_id: str | None = None,
    audit_json_key: str | None = None,
) -> RunReport:
    """Execute a pipeline with a single source-of-truth run ID."""
    actual_run_id = run_id or make_run_id()
    target_repo = cfg.repo_path_for(pipeline_id)
    branch = f"{cfg.rms_qa_branch_prefix}{pipeline_id}/{actual_run_id}"
    tasks: list[dict[str, Any]] = []
    error: str | None = None
    baseline_validation: ValidationSummary | None = None
    pull_request: PullRequestInfo | None = None
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
        if pipeline_id == "website":
            if not audit_json_key:
                raise RuntimeError(
                    "website pipeline requires the exact AIMS website-audit.json R2 key"
                )
            audit = await asyncio.to_thread(
                audit_reader.read_report_key,
                pipeline_id,
                r2,
                cfg.r2_bucket_audits,
                audit_json_key,
                max_object_bytes=cfg.rms_max_audit_object_bytes,
            )
            if not audit:
                raise RuntimeError(
                    "website audit JSON could not be read or failed schema validation"
                )
        else:
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
        await asyncio.to_thread(_run_optimisation_cycle, pipeline_id, cfg, r2)
        issues = await asyncio.to_thread(
            issue_normaliser.normalise, audit, pipeline_id, _date(), cfg, router
        )
        code_fix_limit = (
            cfg.rms_website_max_issues_per_run
            if pipeline_id == "website"
            else cfg.rms_max_issues_per_run
        )
        queues = task_ranker.rank(issues, code_fix_limit)
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
                    cfg.github_token_value,
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

        if not dry_run and cfg.rms_create_pr:
            try:
                pull_request = await asyncio.to_thread(
                    _create_automatic_pr,
                    pipeline_id=pipeline_id,
                    run_id=actual_run_id,
                    branch=branch,
                    audit_json_key=audit_json_key,
                    tasks=tasks,
                    cfg=cfg,
                )
            except Exception as exc:
                logger.exception(
                    "pipeline: automatic pull-request creation failed pipeline=%s runId=%s",
                    pipeline_id,
                    actual_run_id,
                )
                error = _append_error(error, f"automatic pull request failed: {exc}")

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
            pull_request=pull_request,
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
            pull_request=pull_request,
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
    audit_json_key: str | None = None,
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
            audit_json_key=audit_json_key,
        )
    )


# ---------------------------------------------------------------------------
# Optimisation subsystem wiring
#
# The optimisation subsystem's internals (trend analysis, confidence
# scoring, experiments, rollback) previously had no call site: nothing ever
# read AIMS's QA events into them. This section is that call site. It is:
#
#   * Off by default -- gated entirely behind `cfg.rms_optimisation_enabled`
#     (the existing kill switch), so a fresh deploy behaves exactly as
#     before until that flag is explicitly turned on.
#   * Fail-soft -- any error while building the engine stack or running one
#     cycle is logged and swallowed; it can never turn a normal audit run
#     into a failed one.
#   * Scoped to the `on-brand` pipeline -- that is the RAMS pipeline mapped
#     to the AIMS repo (see `Settings.repo_path_for`), and AIMS is the only
#     current producer of `qa-events/{day}/*.json`.
# ---------------------------------------------------------------------------


@dataclass
class _OptimisationStack:
    """The wired-together optimisation engine plus the history store it uses.

    Kept as a pair (rather than reaching into `engine`'s private attributes)
    so this module can record its own bookkeeping entries -- e.g. the
    `manual_review` note for a deferred `auto_configure` action -- through
    the same public-ish surface the engine itself writes through.
    """

    engine: Any  # repo_mgmt.optimisation.optimisation_engine.OptimisationEngine
    history: Any  # repo_mgmt.optimisation.history.OptimisationHistoryStore


def _build_optimisation_stack(cfg: Settings) -> "_OptimisationStack | None":
    """Build the optimisation engine stack from `cfg`, or None if disabled/unavailable.

    Never raises: a missing/invalid policy file, or any other construction
    failure, is logged and treated the same as the feature being disabled,
    so a broken policy file can never take down a normal pipeline run.
    """
    if not cfg.rms_optimisation_enabled:
        return None
    try:
        from repo_mgmt.optimisation.confidence_engine import ConfidenceEngine
        from repo_mgmt.optimisation.experiment_manager import ExperimentManager
        from repo_mgmt.optimisation.history import OptimisationHistoryStore
        from repo_mgmt.optimisation.optimisation_engine import OptimisationEngine
        from repo_mgmt.optimisation.policy import load_policy
        from repo_mgmt.optimisation.rollback_manager import RollbackManager
        from repo_mgmt.optimisation.trend_analysis import TrendAnalyser

        policy = load_policy(cfg.rms_optimisation_policy_path or None)
        history = OptimisationHistoryStore(cfg.rms_optimisation_state_dir)
        rollback = RollbackManager(
            cfg.rms_optimisation_rollback_dir, keep_snapshots=policy.rollback.keep_snapshots
        )
        experiments = ExperimentManager(
            history,
            rollback,
            cooldown_hours=policy.oscillation.min_reoptimisation_interval_hours,
            reversal_lookback=policy.oscillation.reversal_lookback,
        )
        engine = OptimisationEngine(
            policy=policy,
            history=history,
            trend_analyser=TrendAnalyser(policy, history),
            confidence_engine=ConfidenceEngine(policy),
            experiment_manager=experiments,
        )
        return _OptimisationStack(engine=engine, history=history)
    except Exception:
        logger.warning("optimisation subsystem unavailable; skipping this cycle", exc_info=True)
        return None


def _route_optimisation_action(stack: "_OptimisationStack", action: Any) -> None:
    """Route one optimisation action, deferring `auto_configure` to manual review.

    `auto_configure` routing requires a real `apply_fn`/`verify_fn` pair for
    the specific configuration surface being changed (e.g. a scheduler
    interval, a prompt template). No such per-target apply/verify functions
    exist yet against AIMS's live configuration from this repo -- AIMS is a
    separate deployed service, and RAMS has no existing write path into its
    running config. Auto-applying here would mean inventing an untested
    write path into another service exactly where the Experiment Manager
    and Rollback Manager exist to bound risk, not add it. Until concrete
    apply/verify wiring exists for a given category, `auto_configure`-tier
    actions are recorded for manual review instead of being silently
    downgraded or applied with a no-op apply_fn.
    """
    if action.tier == "auto_configure":
        stack.history.append(
            action.pipeline,
            {
                "type": "manual_review",
                "signature": action.signature,
                "action_id": action.action_id,
                "detail": (
                    "auto_configure tier reached but no apply_fn/verify_fn is wired for "
                    f"category {action.category!r} yet; recorded for manual review instead "
                    "of auto-applying"
                ),
            },
        )
        return
    stack.engine.route(action)


def _run_optimisation_cycle(pipeline_id: PipelineId, cfg: Settings, r2: Any) -> None:
    """Ingest AIMS QA evidence and route any newly-eligible optimisation actions.

    This is the call site described in the deployment readiness review: it
    connects AIMS's `qaEvents.js` output (`qa-events/{day}/*.json` in the
    audits bucket) to the previously-unreachable optimisation subsystem.
    Entirely additive and fail-soft -- see the module docstring above.
    """
    if pipeline_id != "on-brand":
        return
    stack = _build_optimisation_stack(cfg)
    if stack is None:
        return
    try:
        from repo_mgmt.optimisation.qa_event_adapter import (
            QaEventWatermark,
            ingest_new_qa_events,
        )

        watermark = QaEventWatermark(
            Path(cfg.rms_optimisation_state_dir) / "qa_event_watermarks"
        )
        summary = ingest_new_qa_events(
            r2=r2,
            bucket=cfg.r2_bucket_audits,
            pipeline=pipeline_id,
            engine=stack.engine,
            watermark=watermark,
        )
        for signature in summary.new_signatures:
            action = stack.engine.evaluate(pipeline_id, signature)
            if action is None:
                continue
            _route_optimisation_action(stack, action)
    except Exception:
        logger.warning("optimisation cycle failed for pipeline %s", pipeline_id, exc_info=True)
