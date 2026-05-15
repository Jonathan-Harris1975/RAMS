"""Per-task orchestration for RAMS code_fix tasks."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repo_mgmt import context_builder, patch_applier, patch_planner, validation_runner
from repo_mgmt.git_manager import TaskRepoSnapshot
from repo_mgmt.patch_protocol import (
    PathTraversalError,
    ProtectedPathError,
    validate_patch,
)

if TYPE_CHECKING:
    from repo_mgmt.config import PipelineId, Settings
    from repo_mgmt.git_manager import GitManager
    from repo_mgmt.model_router import ModelRouter

logger = logging.getLogger(__name__)


def _change_files(patch_doc: dict[str, Any]) -> list[str]:
    """Return de-duplicated file paths named by an AnchorPatch document."""
    files: list[str] = []
    seen: set[str] = set()
    for change in patch_doc.get("changes", []):
        if isinstance(change, dict):
            path = str(change.get("file", ""))
            if path and path not in seen:
                seen.add(path)
                files.append(path)
    return files


def _restore_after_failure(
    git_mgr: "GitManager | None",
    snapshot: TaskRepoSnapshot | None,
    task: dict[str, Any],
) -> None:
    """Best-effort task-scoped rollback used by all live failure paths."""
    if git_mgr is None or snapshot is None:
        return
    git_mgr.restore_task_state(snapshot)
    task["reverted"] = True


async def run_task(
    issue: dict[str, Any],
    target_repo: Path,
    pipeline_id: "PipelineId",
    cfg: "Settings",
    model_router: "ModelRouter",
    git_mgr: "GitManager | None",
    dry_run: bool,
) -> dict[str, Any]:
    """
    Build context, plan a bounded patch, and optionally apply/validate/commit it.

    Dry-run intentionally stops after context and planning. Live mode captures the
    exact task-touched file state before applying a patch and restores that state
    after any validation, stage, commit, push, or unexpected post-apply failure.
    """
    task = dict(issue)
    task_id = str(task.get("taskId", "<unknown>"))
    snapshot: TaskRepoSnapshot | None = None
    patch_started = False

    try:
        if not dry_run and git_mgr is None:
            task["status"] = "manual_review"
            task["error"] = "live mode requires a valid Git safety/revert handle"
            return task

        context_builder.load_context(task.get("affectedPaths", []), target_repo)
        patch_doc = await patch_planner.plan_async(
            task, target_repo, pipeline_id, cfg, model_router
        )
        validate_patch(patch_doc)
        task["patch"] = patch_doc

        if not patch_doc.get("changes"):
            task["status"] = "planned"
            task["modified_files"] = []
            return task

        modified_candidates = _change_files(patch_doc)

        if dry_run:
            task["status"] = "planned"
            task["modified_files"] = modified_candidates
            logger.info(
                "update_executor [%s]: dry-run planned %d change(s); no writes performed",
                task_id,
                len(patch_doc.get("changes", [])),
            )
            return task

        if git_mgr is None:
            task["status"] = "manual_review"
            task["error"] = "missing Git manager before patch application"
            return task

        git_mgr.assert_write_allowed()
        snapshot = git_mgr.capture_task_state(modified_candidates)

        patch_started = True
        modified = patch_applier.apply(
            patch_doc,
            target_repo,
            dry_run=False,
            pipeline_id=pipeline_id,
        )
        task["modified_files"] = modified

        if cfg.rms_validate_after_each_task:
            validation = validation_runner.run(
                pipeline_id, target_repo, cfg, dry_run=False
            )
            task["validation"] = {
                "commands": validation.commands,
                "passed": validation.passed,
                "outputTail": validation.output_tail,
            }
            if not validation.passed:
                logger.warning(
                    "update_executor [%s]: validation failed command=%s returnCode=%s outputTail=%s",
                    task_id,
                    validation.failed_command or "<unknown>",
                    validation.return_code,
                    validation.output_tail[-2000:],
                )
                _restore_after_failure(git_mgr, snapshot, task)
                task["validation_passed"] = False
                task["status"] = "manual_review"
                task["error"] = (
                    f"validation failed: {validation.failed_command or '<unknown>'}"
                )
                return task

        git_mgr.stage_task_files(modified)
        message = f"rms({pipeline_id}): {task_id} - {task.get('title', 'fix')}"
        sha = git_mgr.commit(message)
        branch = git_mgr.current_branch()
        git_mgr.push_branch(branch)
        task.update(
            status="committed",
            commit_sha=sha,
            commit_message=message,
            validation_passed=True,
            reverted=False,
        )
        return task
    except (PathTraversalError, ProtectedPathError) as exc:
        if patch_started:
            try:
                _restore_after_failure(git_mgr, snapshot, task)
            except Exception:
                logger.exception(
                    "update_executor [%s]: failed to restore after protected/path error",
                    task_id,
                )
        task["status"] = "manual_review"
        task["error"] = str(exc)
        task["evidence"] = task.get("evidence", []) + [str(exc)]
    except Exception as exc:
        if patch_started or snapshot is not None:
            try:
                _restore_after_failure(git_mgr, snapshot, task)
            except Exception as revert_exc:
                logger.exception(
                    "update_executor [%s]: rollback failed after task failure", task_id
                )
                task["rollback_error"] = str(revert_exc)
        logger.exception("update_executor [%s]: task failed", task_id)
        task["status"] = "manual_review"
        task["error"] = str(exc)
        task["evidence"] = task.get("evidence", []) + [str(exc)]
    return task
