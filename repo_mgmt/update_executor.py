"""
Update executor for the Repo Management Suite.

Executes a single code_fix task end-to-end:
  1. Build context (context_builder)
  2. Plan patch (patch_planner)
  3. Apply patch (patch_applier)
  4. Run validation (validation_runner)
  5. Stage and commit (git_manager), or revert on failure

Returns a TaskReport for inclusion in the RunReport.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repo_mgmt import patch_applier, patch_planner
from repo_mgmt.patch_protocol import PathTraversalError, ProtectedPathError
from repo_mgmt.report_publisher import TaskReport
from repo_mgmt.validation_runner import run_commands

if TYPE_CHECKING:
    from repo_mgmt.config import Settings
    from repo_mgmt.context_builder import ContextBuilder
    from repo_mgmt.git_manager import GitManager
    from repo_mgmt.model_router import ModelRouter

logger = logging.getLogger(__name__)


def run_task(
    issue: dict[str, Any],
    target_repo: Path,
    pipeline_id: str,
    validation_commands: list[str],
    cfg: "Settings",
    model_router: "ModelRouter",
    git_mgr: "GitManager",
    dry_run: bool,
) -> TaskReport:
    """
    Execute one code_fix task and return a TaskReport.

    Args:
        issue: NormalisedIssue dict with classification='code_fix'.
        target_repo: Absolute path to the repository clone.
        pipeline_id: Pipeline identifier string.
        validation_commands: Ordered validation shell commands.
        cfg: Validated RMS settings.
        model_router: Initialised ModelRouter.
        git_mgr: Initialised GitManager for this run.
        dry_run: If True, skip filesystem writes and git operations.

    Returns:
        Populated TaskReport.
    """
    task_id: str = issue.get("taskId", "<unknown>")
    affected_paths: list[str] = issue.get("affectedPaths", [])

    report = TaskReport(
        task_id=task_id,
        classification="code_fix",
        status="skipped",
        affected_paths=affected_paths,
    )

    try:
        # Step 1: Build file context for planner
        from repo_mgmt.context_builder import load_context
        context_files = load_context(affected_paths, target_repo)

        # Step 2: Plan the patch
        patch_doc = patch_planner.plan(
            issue=issue,
            context_files=context_files,
            model_router=model_router,
        )

        if not patch_doc.get("changes"):
            logger.info("update_executor [%s]: planner returned no changes", task_id)
            report.status = "skipped"
            return report

        report.patch_ops = len(patch_doc["changes"])

        # Step 3: Apply
        modified = patch_applier.apply(
            patch_doc=patch_doc,
            target_repo=target_repo,
            dry_run=dry_run,
            pipeline_id=pipeline_id,
        )

        if dry_run:
            report.status = "applied"
            return report

        # Step 4: Validate
        val_result = run_commands(validation_commands, cwd=target_repo)
        report.validation_passed = val_result.passed

        if not val_result.passed:
            logger.warning(
                "update_executor [%s]: validation failed — reverting", task_id
            )
            git_mgr.revert()
            report.status = "reverted"
            report.error = f"validation failed:\n{val_result.output_tail[-500:]}"
            return report

        # Step 5: Stage and commit
        git_mgr.stage_task_files(modified)
        sha = git_mgr.commit(
            f"rms({pipeline_id}): {task_id} — {issue.get('title', 'fix')}"
        )
        report.commit_sha = sha
        report.status = "applied"

    except (PathTraversalError, ProtectedPathError) as exc:
        logger.error("update_executor [%s]: safety error: %s", task_id, exc)
        report.status = "skipped"
        report.error = str(exc)

    except Exception as exc:
        logger.exception("update_executor [%s]: unexpected error", task_id)
        report.status = "skipped"
        report.error = str(exc)

    return report
