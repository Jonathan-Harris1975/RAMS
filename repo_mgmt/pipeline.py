"""
Pipeline orchestrator for the Repo Management Suite.

RmsPipeline encapsulates all configuration for a single audit pipeline and
runs the full cycle:
  1. audit_reader    — fetch latest R2 audit snapshot
  2. issue_normaliser — convert findings to NormalisedIssue list
  3. task_ranker      — rank and cap the code_fix queue
  4. update_executor  — apply each code_fix task
  5. report_publisher — write RunReport to R2 or local disk

Usage:
    pipeline = RmsPipeline.for_id("on-brand", cfg, r2, model_router)
    report   = await pipeline.run(dry_run=True)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repo_mgmt import audit_reader, issue_normaliser, update_executor
from repo_mgmt.git_manager import GitManager
from repo_mgmt.model_router import ModelRouter
from repo_mgmt.report_publisher import (
    RunReport,
    TaskReport,
    ValidationSummary,
    make_run_id,
    publish,
)
from repo_mgmt.task_ranker import rank
from repo_mgmt.validation_runner import run_commands

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId, Settings
    from repo_mgmt.r2_client import R2Client

logger = logging.getLogger(__name__)

# Per-pipeline protected path sets  (also enforced by patch_applier)
_PROTECTED: dict[str, frozenset[str]] = {
    "mobile-ux": frozenset(
        [
            "blog/posts/",
            "blog/posts.json",
            "transcripts/",
            "data/podcast-episodes.json",
            "assets/js/podcast-transcripts.min.js",
            "functions/transcripts/",
        ]
    ),
    "on-brand": frozenset(),
    "seo-aeo-geo": frozenset(),
}

_APPROVED_FIX_CLASSES: dict[str, frozenset[str]] = {
    "seo-aeo-geo": frozenset(
        ["route_fix", "config_fix", "schema_fix", "prompt_template_update",
         "audit_output_fix", "middleware_fix"]
    ),
    "mobile-ux": frozenset(
        ["html_fix", "css_fix", "meta_fix", "viewport_fix", "accessibility_fix",
         "template_fix", "partial_fix"]
    ),
    "on-brand": frozenset(
        ["html_fix", "template_fix", "schema_fix", "meta_fix", "partial_fix"]
    ),
}


class RmsPipeline:
    """Full audit-to-patch pipeline for a single pipeline identifier."""

    def __init__(
        self,
        pipeline_id: "PipelineId",
        cfg: "Settings",
        r2: "R2Client",
        model_router: "ModelRouter",
    ) -> None:
        """
        Initialise an RmsPipeline.

        Args:
            pipeline_id: One of 'seo-aeo-geo', 'mobile-ux', 'on-brand'.
            cfg: Validated RMS settings.
            r2: Initialised R2Client.
            model_router: Initialised ModelRouter for LLM calls.
        """
        self.pipeline_id = pipeline_id
        self._cfg = cfg
        self._r2 = r2
        self._model_router = model_router

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def audit_key(self) -> str:
        """R2 audit key for this pipeline."""
        return f"audits/{self.pipeline_id}/latest.json"

    @property
    def target_repo(self) -> Path:
        """Absolute path to the target repository."""
        return self._cfg.repo_path_for(self.pipeline_id)

    @property
    def validation_commands(self) -> list[str]:
        """Ordered validation commands for this pipeline."""
        return self._cfg.validation_commands_for(self.pipeline_id)

    @property
    def protected_paths(self) -> frozenset[str]:
        """Protected path prefixes for this pipeline."""
        return _PROTECTED.get(self.pipeline_id, frozenset())

    @property
    def approved_fix_classes(self) -> frozenset[str]:
        """Approved fix class names for this pipeline."""
        return _APPROVED_FIX_CLASSES.get(self.pipeline_id, frozenset())

    # ── Factory ────────────────────────────────────────────────────────────

    @classmethod
    def for_id(
        cls,
        pipeline_id: "PipelineId",
        cfg: "Settings",
        r2: "R2Client",
        model_router: "ModelRouter",
    ) -> "RmsPipeline":
        """
        Create an RmsPipeline for the given *pipeline_id*.

        Args:
            pipeline_id: Pipeline identifier.
            cfg: Validated settings.
            r2: Initialised R2Client.
            model_router: Initialised ModelRouter.

        Returns:
            Ready-to-run RmsPipeline.
        """
        return cls(pipeline_id, cfg, r2, model_router)

    # ── Run ────────────────────────────────────────────────────────────────

    async def run(
        self,
        dry_run: bool,
        run_id: str | None = None,
    ) -> RunReport:
        """
        Execute the full pipeline cycle.

        Args:
            dry_run: If True, plan and log changes without writing or committing.
            run_id: Optional caller-supplied run identifier (ISO-UTC string).
                    Generated automatically if not supplied.

        Returns:
            Completed RunReport.
        """
        if run_id is None:
            run_id = make_run_id()

        started = datetime.now(tz=timezone.utc).isoformat()
        logger.info(
            "pipeline [%s]: starting run %s (dry_run=%s)",
            self.pipeline_id, run_id, dry_run,
        )

        # Step 1: read audit
        raw_audit = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: audit_reader.read_latest(
                self.pipeline_id, self._r2, self._cfg.r2_bucket_audits
            ),
        )

        # Step 2: normalise
        issues = issue_normaliser.normalise(
            raw_findings=raw_audit.get("findings", []),
            pipeline_id=self.pipeline_id,
            cfg=self._cfg,
            model_router=self._model_router,
        )

        # Step 3: rank
        queues = rank(issues, max_code_fix=self._cfg.rms_max_issues_per_run)

        task_reports: list[TaskReport] = []
        commits: list[str] = []
        branch_name: str | None = None

        # Step 4: create git branch and run each code_fix task
        git_mgr = GitManager(
            target_repo=self.target_repo,
            branch_prefix=self._cfg.rms_qa_branch_prefix,
            push_enabled=self._cfg.rms_push_enabled,
            revert_on_failure=self._cfg.rms_revert_on_validation_failure,
        )

        if queues.code_fix and not dry_run:
            try:
                branch_name = git_mgr.create_branch(self.pipeline_id, run_id)
            except Exception as exc:
                logger.error("pipeline [%s]: could not create branch: %s", self.pipeline_id, exc)

        for issue in queues.code_fix:
            task_report = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda i=issue: update_executor.run_task(
                    issue=i,
                    target_repo=self.target_repo,
                    pipeline_id=self.pipeline_id,
                    validation_commands=self.validation_commands,
                    cfg=self._cfg,
                    model_router=self._model_router,
                    git_mgr=git_mgr,
                    dry_run=dry_run,
                ),
            )
            task_reports.append(task_report)
            if task_report.commit_sha:
                commits.append(task_report.commit_sha)

        # Future guidance and manual review tasks become simple TaskReport entries
        for issue in queues.future_guidance:
            task_reports.append(
                TaskReport(
                    task_id=issue.get("taskId", "<unknown>"),
                    classification="future_guidance",
                    status="future_guidance",
                    affected_paths=issue.get("affectedPaths", []),
                )
            )

        for issue in queues.manual_review:
            task_reports.append(
                TaskReport(
                    task_id=issue.get("taskId", "<unknown>"),
                    classification="manual_review",
                    status="manual_review",
                    affected_paths=issue.get("affectedPaths", []),
                )
            )

        # Step 5: final validation (only in live mode with commits)
        val_summary: ValidationSummary | None = None
        if commits and not dry_run:
            val_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: run_commands(self.validation_commands, cwd=self.target_repo),
            )
            val_summary = ValidationSummary(
                passed=val_result.passed,
                output_tail=val_result.output_tail,
            )
            if not val_result.passed:
                logger.error(
                    "pipeline [%s]: final validation failed", self.pipeline_id
                )

        # Step 6: push branch
        if branch_name and commits and not dry_run:
            try:
                git_mgr.push_branch(branch_name)
            except Exception as exc:
                logger.warning("pipeline [%s]: push failed: %s", self.pipeline_id, exc)

        # Build summary counters
        summary = {
            "total": len(issues),
            "code_fix": len(queues.code_fix),
            "applied": sum(1 for t in task_reports if t.status == "applied"),
            "reverted": sum(1 for t in task_reports if t.status == "reverted"),
            "skipped": sum(1 for t in task_reports if t.status == "skipped"),
            "future_guidance": len(queues.future_guidance),
            "manual_review": len(queues.manual_review),
        }

        report = RunReport(
            runId=run_id,
            pipeline=self.pipeline_id,
            targetRepo=str(self.target_repo),
            branch=branch_name,
            dryRun=dry_run,
            summary=summary,
            tasks=task_reports,
            validation=val_summary,
            commits=commits,
        )

        # Step 7: publish report
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: publish(report, self._cfg, self._r2),
            )
        except Exception as exc:
            logger.error(
                "pipeline [%s]: report publish failed: %s", self.pipeline_id, exc
            )

        logger.info(
            "pipeline [%s]: finished run %s — applied=%d reverted=%d",
            self.pipeline_id, run_id,
            summary["applied"], summary["reverted"],
        )
        return report
