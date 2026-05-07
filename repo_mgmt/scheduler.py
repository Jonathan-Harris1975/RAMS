"""
APScheduler-based cron scheduler for the Repo Management Suite.

Runs all three pipelines on the cron schedule defined in RMS_SCHEDULE_CRON
(default: 0 3 * * * — 03:00 UTC daily).

Designed to be started once alongside the FastAPI server.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from repo_mgmt import pipeline
from repo_mgmt.config import PipelineId

if TYPE_CHECKING:
    from repo_mgmt.config import Settings
    from repo_mgmt.r2_client import R2Client

logger = logging.getLogger(__name__)

_ALL_PIPELINES: list[PipelineId] = ["seo-aeo-geo", "mobile-ux", "on-brand"]


def _run_all(cfg: "Settings", r2: "R2Client") -> None:
    """Scheduled job: run all three pipelines sequentially."""
    logger.info("scheduler: starting scheduled run of all pipelines")
    for pid in _ALL_PIPELINES:
        try:
            report = pipeline.run(pid, cfg, r2)
            logger.info(
                "scheduler: [%s] completed — applied=%d reverted=%d",
                pid,
                report.issues_applied,
                report.issues_reverted,
            )
        except Exception as exc:
            logger.exception("scheduler: [%s] unexpected error: %s", pid, exc)
    logger.info("scheduler: scheduled run complete")


def build_scheduler(cfg: "Settings", r2: "R2Client") -> BackgroundScheduler:
    """
    Create and configure the APScheduler BackgroundScheduler.

    The scheduler is NOT started here — call .start() on the returned object.

    Args:
        cfg: Validated RMS settings.
        r2: Initialised R2Client.

    Returns:
        Configured BackgroundScheduler (not yet started).
    """
    scheduler = BackgroundScheduler(timezone="UTC")
    trigger = CronTrigger.from_crontab(cfg.rms_schedule_cron, timezone="UTC")
    scheduler.add_job(
        func=_run_all,
        trigger=trigger,
        kwargs={"cfg": cfg, "r2": r2},
        id="rms-all-pipelines",
        name="RMS: run all pipelines",
        replace_existing=True,
    )
    logger.info(
        "scheduler: configured cron job with schedule %r", cfg.rms_schedule_cron
    )
    return scheduler
