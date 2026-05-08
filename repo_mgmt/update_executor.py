"""
Update executor for the Repo Management Suite.

Executes a single code_fix task end-to-end:
  1. Build context (context_builder)
  2. Plan patch (patch_planner)
  3. [dry_run=True]  mark planned, return — no fs ops, no git
  4. [dry_run=False] branch-safety preflight
  5. Apply patch (patch_applier)
  6. Validate (validation_runner)
  7. If validation fails and RMS_REVERT_ON_VALIDATION_FAILURE=true, revert
  8. Commit only after validation passes
  9. Push only if enabled

One failed task never aborts the whole run.
Returns the updated NormalisedIssue dict with status set.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repo_mgmt import patch_applier, patch_planner
from repo_mgmt.patch_protocol import PathTraversalError, ProtectedPathError
from repo_mgmt.validation_runner import run_commands

if TYPE_CHECKING:
    from repo_mgmt.config import Settings
    from repo_mgmt.git_manager import GitManager
    from repo_mgmt.model_router import ModelRouter

logger = logging.getLogger(__name__)


def run_task(
    issue: dict[str, Any],
    target_repo: Path,
    pipeline_id: str,
    validation_commands: list[str],
    protected_paths: "frozenset[str]",
    cfg: "Settings",
    model_router: "ModelRouter",
    git_mgr: "GitManager",
    dry_run: bool,
) -> dict[str, Any]:
    """
    Execute one code_fix task and return the updated NormalisedIssue dict.

    Args:
        issue: NormalisedIssue dict with classification='code_fix'.
        target_repo: Absolute path to the repository clone.
        pipeline_id: Pipeline identifier string.
        validation_commands: Ordered validation shell commands.
        protected_paths: Frozenset of protected path prefixes for this pipeline.
        cfg: Validated RMS settings.
        model_router: Initialised ModelRouter.
        git_mgr: Initialised GitManager for this run.
        dry_run: If True, skip filesystem writes and git operations.

    Returns:
        Updated NormalisedIssue dict with 'status' and optional commit fields.
    """
    task = dict(issue)  # copy so we don't mutate the original
    task_id: str = task.get("taskId", "<unknown>")
    affected_paths: list[str] = task.get("affectedPaths", [])

    try:
        # Step 1: Build file context
        from repo_mgmt.context_builder import load_context
        context_files = load_context(affected_paths, target_repo)

        # Step 2: Plan the patch
        patch_doc = patch_planner.plan(
            issue=task,
            context_files=context_files,
            model_router=model_router,
        )

        if not patch_doc.get("changes"):
            logger.info("update_executor [%s]: planner returned no changes", task_id)
            task["status"] = "planned"
            return task

        # Step 3: dry-run short-circuit — no filesystem or git operations
        if dry_run:
            task["status"] = "planned"
            return task

        # Step 4: Branch-safety preflight BEFORE any file write
        git_mgr._check_not_protected()  # raises BranchSafetyError if on main/master

        # Step 5: Apply patch
        modified = patch_applier.apply(
            patch_doc=patch_doc,
            target_repo=target_repo,
            dry_run=False,
            pipeline_id=pipeline_id,
        )
        task["status"] = "patch_applied"

        # Step 6: Validate
        val_result = run_commands(validation_commands, cwd=target_repo)
        task["validation_passed"] = val_result.passed

        if not val_result.passed:
            logger.warning(
                "update_executor [%s]: validation failed — reverting", task_id
            )
            if cfg.rms_revert_on_validation_failure:
                git_mgr.revert()
                task["status"] = "reverted"
            else:
                task["status"] = "validation_failed"
            task["error"] = f"validation failed:\n{val_result.output_tail[-500:]}"
            return task

        # Step 7: Stage and commit
        git_mgr.stage_task_files(modified)
        commit_msg = f"rms({pipeline_id}): {task_id} — {task.get('title', 'fix')}"
        sha = git_mgr.commit(commit_msg)
        task["commit_sha"] = sha
        task["commit_message"] = commit_msg
        task["status"] = "committed"

    except (PathTraversalError, ProtectedPathError) as exc:
        logger.error("update_executor [%s]: safety error: %s", task_id, exc)
        task["status"] = "manual_review"
        task["error"] = str(exc)
        task["evidence"] = task.get("evidence", []) + [str(exc)]

    except Exception as exc:
        logger.exception("update_executor [%s]: unexpected error", task_id)
        task["status"] = "manual_review"
        task["error"] = str(exc)
        task["evidence"] = task.get("evidence", []) + [str(exc)]

    return task
