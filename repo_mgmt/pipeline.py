"""Active RAMS pipeline orchestration."""
from __future__ import annotations
import asyncio, logging, threading
from datetime import datetime, timezone
from typing import Any
from repo_mgmt import audit_reader, issue_normaliser, task_ranker, update_executor
from repo_mgmt.config import PipelineId, Settings
from repo_mgmt.git_manager import GitManager
from repo_mgmt.model_router import ModelRouter
from repo_mgmt.report_publisher import CommitInfo, RunReport, ValidationSummary, make_run_id, publish
logger=logging.getLogger(__name__)
# ── Concurrency gates ──────────────────────────────────────────────────────
# Two independent gates exist by design:
#   1. api.py `_running` dict  — HTTP layer; returns 409 to callers.
#   2. `_pipeline_locks` below — internal layer; protects against scheduler-
#      or CLI-triggered concurrent runs that bypass the HTTP gate.
# Gate 2 is the authoritative lock for pipeline.run(). If `_running` is
# cleared before the lock releases (e.g. on a crash), gate 2 prevents a
# second run from proceeding. Both gates must be satisfied for a run to start.
_pipeline_locks={p:threading.Lock() for p in ('seo-aeo-geo','mobile-ux','on-brand')}
def is_running(pipeline_id:PipelineId)->bool: return _pipeline_locks[pipeline_id].locked()
def _date()->str: return datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')
def _summary(tasks:list[dict[str,Any]], snapshots:int)->dict[str,int]:
    return {'snapshotsRead':snapshots,'tasksGenerated':len(tasks),'codeFixesAttempted':sum(1 for t in tasks if t.get('classification')=='code_fix'),'committed':sum(1 for t in tasks if t.get('status')=='committed'),'validationFailed':sum(1 for t in tasks if t.get('status') in {'validation_failed','reverted'}),'futureGuidance':sum(1 for t in tasks if t.get('classification')=='future_guidance' or t.get('status')=='future_guidance'),'manualReview':sum(1 for t in tasks if t.get('classification')=='manual_review' or t.get('status')=='manual_review')}
def _val(tasks,cfg,pid):
    for t in reversed(tasks):
        v=t.get('validation')
        if v: return ValidationSummary(commands=v.get('commands',cfg.validation_commands_for(pid)),passed=bool(v.get('passed')),output_tail=str(v.get('outputTail','')))
    return None
def _commits(tasks):
    return [CommitInfo(sha=str(t['commit_sha']),message=str(t.get('commit_message','')),files=list(t.get('modified_files',[]))) for t in tasks if t.get('commit_sha')]
class RmsPipeline:
    def __init__(self,pipeline_id:PipelineId,cfg:Settings,r2:Any,router:ModelRouter): self.pipeline_id=pipeline_id; self.cfg=cfg; self.r2=r2; self.router=router
    @classmethod
    def for_id(cls,pipeline_id:PipelineId,cfg:Settings,r2:Any,router:ModelRouter): return cls(pipeline_id,cfg,r2,router)
    async def run(self,dry_run:bool|None=None)->RunReport: return await _run_async(self.pipeline_id,self.cfg,self.r2,self.router,self.cfg.rms_dry_run if dry_run is None else dry_run)
    @property
    def audit_key(self) -> str:
        """R2 key for this pipeline's latest audit snapshot."""
        return f"audits/{self.pipeline_id}/latest.json"
    @property
    def target_repo(self):
        """Absolute path to the target repository clone."""
        return self.cfg.repo_path_for(self.pipeline_id)
    @property
    def validation_commands(self) -> list[str]:
        """Ordered validation commands for this pipeline."""
        return self.cfg.validation_commands_for(self.pipeline_id)
    @property
    def protected_paths(self) -> frozenset:
        """Repo-relative path prefixes that this pipeline may not modify."""
        from repo_mgmt.patch_applier import PROTECTED_PATHS
        return PROTECTED_PATHS.get(self.pipeline_id, frozenset())
    @property
    def approved_fix_classes(self) -> frozenset:
        """Fix classes this pipeline is permitted to apply."""
        _approved: dict = {
            "seo-aeo-geo": frozenset({
                "route_fix", "config_fix", "schema_fix",
                "prompt_template_update", "audit_output_fix", "middleware_fix",
            }),
            "mobile-ux": frozenset({
                "html_fix", "css_fix", "meta_fix",
                "viewport_fix", "accessibility_fix", "redirect_fix",
            }),
            "on-brand": frozenset({
                "html_fix", "css_fix", "template_fix", "partial_fix",
                "redirect_fix", "prompt_template_update", "schema_fix", "meta_fix",
            }),
        }
        return _approved.get(self.pipeline_id, frozenset())
async def _run_async(pipeline_id:PipelineId,cfg:Settings,r2:Any,router:ModelRouter,dry_run:bool)->RunReport:
    lock=_pipeline_locks[pipeline_id]; run_id=make_run_id(); target_repo=cfg.repo_path_for(pipeline_id); branch=''; tasks=[]; error=None; snapshots=0
    if not lock.acquire(False): return RunReport(runId=run_id,pipeline=pipeline_id,targetRepo=str(target_repo),branch='',dryRun=dry_run,summary=_summary([],0),tasks=[],validation=None,commits=[],error=f"pipeline {pipeline_id!r} already running")
    try:
        audit=audit_reader.read_latest(pipeline_id,r2,cfg.r2_bucket_audits); snapshots=1 if audit else 0
        issues=issue_normaliser.normalise(audit,pipeline_id,_date(),cfg,router); queues=task_ranker.rank(issues,cfg.rms_max_issues_per_run); selected=[*queues.code_fix,*queues.manual_review,*queues.future_guidance]
        git_mgr=None
        if not dry_run and queues.code_fix:
            try:
                branch=f"{cfg.rms_qa_branch_prefix}{pipeline_id}/{run_id}"; git_mgr=GitManager(target_repo,cfg.rms_qa_branch_prefix,cfg.rms_push_enabled); git_mgr.create_branch(branch)
            except Exception as exc:
                error=f"Git branch setup failed; live code_fix writes skipped: {exc}"
                for issue in selected:
                    t=dict(issue)
                    if t.get('classification')=='code_fix': t['status']='manual_review'; t['error']=error
                    tasks.append(t)
                selected=[]
        for issue in selected:
            if issue.get('classification')=='code_fix': tasks.append(await update_executor.run_task(issue,target_repo,pipeline_id,cfg,router,git_mgr,dry_run))
            else: tasks.append(dict(issue))
        report=RunReport(runId=run_id,pipeline=pipeline_id,targetRepo=str(target_repo),branch=branch,dryRun=dry_run,summary=_summary(tasks,snapshots),tasks=tasks,validation=_val(tasks,cfg,pipeline_id),commits=_commits(tasks),error=error)
        try: publish(report,cfg,r2)
        except Exception as exc: report.error=(report.error+'; ' if report.error else '')+f'report publish failed: {exc}'
        return report
    except Exception as exc:
        report=RunReport(runId=run_id,pipeline=pipeline_id,targetRepo=str(target_repo),branch=branch,dryRun=dry_run,summary=_summary(tasks,snapshots),tasks=tasks,validation=_val(tasks,cfg,pipeline_id),commits=_commits(tasks),error=str(exc))
        try: publish(report,cfg,r2)
        except Exception: pass
        return report
    finally: lock.release()
def run(pipeline_id:PipelineId,cfg:Settings,r2:Any,dry_run:bool|None=None)->RunReport:
    return asyncio.run(_run_async(pipeline_id,cfg,r2,ModelRouter(cfg),cfg.rms_dry_run if dry_run is None else dry_run))
