"""APScheduler-based cron scheduler for RAMS."""
from __future__ import annotations
import asyncio, logging
from typing import TYPE_CHECKING
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
except ImportError:
    BackgroundScheduler=None; CronTrigger=None
if TYPE_CHECKING:
    from repo_mgmt.config import Settings
    from repo_mgmt.r2_client import R2Client
logger=logging.getLogger(__name__); _ALL_PIPELINES=['seo-aeo-geo','mobile-ux','on-brand']
def _run_all(cfg:'Settings', r2:'R2Client')->None:
    from repo_mgmt.model_router import ModelRouter
    from repo_mgmt.pipeline import RmsPipeline
    router=ModelRouter(cfg)
    for pid in _ALL_PIPELINES:
        report=asyncio.run(RmsPipeline.for_id(pid,cfg,r2,router).run(dry_run=cfg.rms_dry_run)) # type: ignore[arg-type]
        logger.info('scheduler: [%s] completed committed=%d futureGuidance=%d', pid, report.summary.get('committed',0), report.summary.get('futureGuidance',0))
def build_scheduler(cfg:'Settings', r2:'R2Client'):
    if BackgroundScheduler is None or CronTrigger is None: raise RuntimeError('APScheduler is not installed; scheduler cannot be built')
    sched=BackgroundScheduler(timezone='UTC'); trig=CronTrigger.from_crontab(cfg.rms_schedule_cron, timezone='UTC')
    sched.add_job(_run_all,trigger=trig,kwargs={'cfg':cfg,'r2':r2},id='rms-all-pipelines',name='RMS: run all pipelines',replace_existing=True,max_instances=1); return sched
