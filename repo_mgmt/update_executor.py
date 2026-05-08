"""Per-task orchestration for RAMS code_fix tasks."""
from __future__ import annotations
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from repo_mgmt import context_builder, patch_applier, patch_planner, validation_runner
from repo_mgmt.patch_protocol import PathTraversalError, ProtectedPathError, validate_patch
if TYPE_CHECKING:
    from repo_mgmt.config import Settings
    from repo_mgmt.git_manager import GitManager
    from repo_mgmt.model_router import ModelRouter
logger=logging.getLogger(__name__)
async def run_task(issue:dict[str,Any], target_repo:Path, pipeline_id:str, cfg:'Settings', model_router:'ModelRouter', git_mgr:'GitManager|None', dry_run:bool)->dict[str,Any]:
    task=dict(issue); tid=str(task.get('taskId','<unknown>'))
    try:
        if not dry_run and git_mgr is None:
            task['status']='manual_review'; task['error']='live mode requires a valid Git safety/revert handle'; return task
        context_builder.load_context(task.get('affectedPaths',[]), target_repo)
        patch_doc=patch_planner.plan(task,target_repo,pipeline_id,cfg,model_router); validate_patch(patch_doc); task['patch']=patch_doc
        if not patch_doc.get('changes'):
            task['status']='planned'; task['modified_files']=[]; return task
        if not dry_run and git_mgr is not None: git_mgr.assert_write_allowed()
        modified=patch_applier.apply(patch_doc,target_repo,dry_run=dry_run,pipeline_id=pipeline_id); task['modified_files']=modified
        if dry_run: task['status']='planned'; return task
        validation=validation_runner.run(pipeline_id,target_repo,cfg,dry_run=False)
        task['validation']={'commands':validation.commands,'passed':validation.passed,'outputTail':validation.output_tail}
        if not validation.passed:
            task['validation_passed']=False
            if cfg.rms_revert_on_validation_failure and git_mgr is not None: git_mgr.revert(); task['status']='reverted'
            else: task['status']='validation_failed'
            task['error']=f"validation failed: {validation.failed_command or '<unknown>'}"; return task
        if git_mgr is None: task['status']='manual_review'; task['error']='missing Git manager after validation'; return task
        git_mgr.stage_task_files(modified); msg=f"rms({pipeline_id}): {tid} - {task.get('title','fix')}"; sha=git_mgr.commit(msg); branch=getattr(git_mgr,'current_branch',lambda:'')(); git_mgr.push_branch(branch)
        task.update(status='committed', commit_sha=sha, commit_message=msg, validation_passed=True); return task
    except (PathTraversalError,ProtectedPathError) as exc:
        task['status']='manual_review'; task['error']=str(exc); task['evidence']=task.get('evidence',[])+[str(exc)]
    except Exception as exc:
        logger.exception('update_executor [%s]: task failed', tid); task['status']='manual_review'; task['error']=str(exc); task['evidence']=task.get('evidence',[])+[str(exc)]
    return task
