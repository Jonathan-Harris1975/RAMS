"""Per-task orchestration for RAMS code_fix tasks."""

from __future__ import annotations

import asyncio

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repo_mgmt import patch_applier, patch_planner, validation_runner
from repo_mgmt.automation_gate import evaluate_phase4c_auto_pr_gate
from repo_mgmt.engineering_council import run_engineering_council
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


_PARTIAL_PATHS = frozenset(
    {"assets/partials/header.html", "assets/partials/footer.html"}
)
_WEBSITE_PIPELINES = frozenset({"website", "seo-aeo-geo", "mobile-ux"})


def _touches_governed_partial(files: list[str]) -> bool:
    """Return True when a patch changes a shared website partial."""
    return any(path in _PARTIAL_PATHS for path in files)


def _html_files(target_repo: Path) -> list[str]:
    """Return all repo-relative HTML files for partial-sync rollback/staging."""
    root = target_repo.resolve()
    files: list[str] = []
    for path in root.rglob("*.html"):
        if ".git" in path.parts:
            continue
        try:
            files.append(path.resolve().relative_to(root).as_posix())
        except ValueError:
            continue
    return sorted(files)


def _snapshot_candidates(
    patch_files: list[str], target_repo: Path, pipeline_id: "PipelineId"
) -> list[str]:
    """Return all paths whose pre-task state must be restorable."""
    candidates = list(patch_files)
    if pipeline_id in _WEBSITE_PIPELINES and _touches_governed_partial(patch_files):
        candidates.extend(_html_files(target_repo))
    return list(dict.fromkeys(candidates))


def _status_paths(git_mgr: "GitManager") -> list[str]:
    """Return repo-relative paths currently changed in Git status."""
    paths: list[str] = []
    for row in git_mgr.status_porcelain():
        if len(row) < 4:
            continue
        path = row[3:]
        if " -> " in path:
            old_path, new_path = path.split(" -> ", 1)
            paths.extend([old_path.strip(), new_path.strip()])
        else:
            paths.append(path.strip())
    return list(dict.fromkeys(path for path in paths if path))


def _post_patch_sync_required(
    patch_files: list[str], pipeline_id: "PipelineId"
) -> bool:
    """Return True when generated HTML must be refreshed before validation."""
    return pipeline_id in _WEBSITE_PIPELINES and _touches_governed_partial(patch_files)


def _run_post_patch_sync(target_repo: Path) -> dict[str, Any]:
    """Propagate changed shared partials into generated static pages."""
    commands = ["python3 scripts/inject_partials.py"]
    result = validation_runner.run_commands(commands, cwd=target_repo)
    return {
        "commands": result.commands,
        "passed": result.passed,
        "outputTail": result.output_tail,
        "failedCommand": result.failed_command,
        "returnCode": result.return_code,
    }


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
    task.setdefault("patchAttempted", False)
    task.setdefault("patchApplied", False)
    task.setdefault("validationFailed", False)
    task.setdefault("commitCreated", False)
    task.setdefault("unsafeWriteRefused", False)
    snapshot: TaskRepoSnapshot | None = None
    patch_started = False
    post_patch_validation: validation_runner.ValidationResult | None = None

    try:
        if not dry_run and git_mgr is None:
            task["status"] = "manual_review"
            task["error"] = "live mode requires a valid Git safety/revert handle"
            return task

        task["patchAttempted"] = True
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
        snapshot_paths = _snapshot_candidates(
            modified_candidates, target_repo, pipeline_id
        )
        snapshot = git_mgr.capture_task_state(snapshot_paths)

        patch_started = True
        modified = patch_applier.apply(
            patch_doc,
            target_repo,
            dry_run=False,
            pipeline_id=pipeline_id,
        )
        task["patchApplied"] = True
        task["modified_files"] = modified

        if _post_patch_sync_required(modified_candidates, pipeline_id):
            sync_result = await asyncio.to_thread(_run_post_patch_sync, target_repo)
            task["postPatchSync"] = sync_result
            if not sync_result["passed"]:
                logger.warning(
                    "update_executor [%s]: post-patch partial sync failed command=%s returnCode=%s outputTail=%s",
                    task_id,
                    sync_result.get("failedCommand") or "<unknown>",
                    sync_result.get("returnCode"),
                    str(sync_result.get("outputTail", ""))[-2000:],
                )
                _restore_after_failure(git_mgr, snapshot, task)
                task["validation_passed"] = False
                task["validationFailed"] = True
                task["status"] = "manual_review"
                task["error"] = (
                    "post-patch partial sync failed: "
                    f"{sync_result.get('failedCommand') or '<unknown>'}"
                )
                return task
            task["modified_files"] = _status_paths(git_mgr)

        if cfg.rms_validate_after_each_task:
            validation = await asyncio.to_thread(
                validation_runner.run, pipeline_id, target_repo, cfg, False
            )
            post_patch_validation = validation
            task["validation"] = {
                "commands": validation.commands,
                "passed": validation.passed,
                "outputTail": validation.output_tail,
                "failedCommand": validation.failed_command,
                "returnCode": validation.return_code,
                "affectedRepo": str(target_repo),
                "actionableHint": None
                if validation.passed
                else (
                    f"Fix the post-patch validation failure, then rerun {validation.failed_command or 'the validation command'}."
                ),
                "patchingSkipped": False,
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
                task["validationFailed"] = True
                task["status"] = "manual_review"
                task["error"] = (
                    f"validation failed: {validation.failed_command or '<unknown>'}"
                )
                return task

        modified = list(task.get("modified_files", modified))
        council = await run_engineering_council(task, patch_doc, cfg, model_router)
        task["engineeringCouncil"] = council
        if council.get("decision") != "approve_micro_surgery":
            logger.warning("update_executor [%s]: engineering council refused autonomous patch", task_id)
            _restore_after_failure(git_mgr, snapshot, task)
            task["status"] = "manual_review"
            task["error"] = "Engineering council refused autonomous patch"
            return task

        phase4c_gate = evaluate_phase4c_auto_pr_gate(
            task=task,
            patch_doc=patch_doc,
            modified_files=modified,
            validation=post_patch_validation,
            council=council,
        )
        task["phase4cGate"] = phase4c_gate.to_report()
        if not phase4c_gate.ok:
            logger.warning(
                "update_executor [%s]: Phase 4C auto-PR gate refused commit: %s",
                task_id,
                "; ".join(phase4c_gate.defects[:8]),
            )
            _restore_after_failure(git_mgr, snapshot, task)
            task["status"] = "manual_review"
            task["error"] = "Phase 4C auto-PR gate failed"
            task["evidence"] = task.get("evidence", []) + phase4c_gate.defects
            return task

        git_mgr.stage_task_files(modified)
        message = f"rms({pipeline_id}): {task_id} - {task.get('title', 'fix')}"
        sha = git_mgr.commit(message)
        branch = git_mgr.current_branch()
        pushed = git_mgr.push_branch(branch)
        task.update(
            status="committed",
            commit_sha=sha,
            commit_message=message,
            validation_passed=True,
            reverted=False,
            patchApplied=True,
            commitCreated=True,
            pushed=pushed,
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
        task["unsafeWriteRefused"] = True
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
