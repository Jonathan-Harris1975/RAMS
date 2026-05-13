"""
FastAPI application for the Repo Management Suite.

The API exposes fixed rebuild endpoints for the three independent pipelines and
keeps per-pipeline in-memory singletons plus running flags for concurrency.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Literal, cast

from fastapi import BackgroundTasks, Body, FastAPI, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from repo_mgmt import pipeline as pipeline_mod
from repo_mgmt.config import PipelineId, Settings, load_settings
from repo_mgmt.model_router import ModelRouter
from repo_mgmt.pipeline import RmsPipeline
from repo_mgmt.r2_client import R2Client
from repo_mgmt.scheduler import build_scheduler

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Repo Management Suite",
    description="Autonomous repository audit and patch pipeline service.",
    version="1.0.0",
)

PipelineIdLiteral = Literal["seo-aeo-geo", "mobile-ux", "on-brand"]
_PIPELINE_IDS: tuple[PipelineIdLiteral, ...] = ("seo-aeo-geo", "mobile-ux", "on-brand")

_pipelines: dict[PipelineId, RmsPipeline] = {}
_running: dict[PipelineId, bool] = {pipeline_id: False for pipeline_id in _PIPELINE_IDS}

_cfg: Settings | None = None
_r2: R2Client | None = None


class RunRequest(BaseModel):
    """Optional body for POST /rebuild/{pipeline_id}/run."""

    dry_run: bool | None = None


def _make_run_id() -> str:
    """Return a UTC run identifier safe for branch names and R2 keys."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _get_cfg() -> Settings | None:
    """Return cached Settings, loading lazily on first use."""
    global _cfg
    if _cfg is None:
        try:
            _cfg = load_settings()
        except Exception as exc:
            logger.warning("api: config not fully loaded: %s", exc)
    return _cfg


def _get_r2() -> R2Client | None:
    """Return the cached R2 client, creating it lazily when config is ready."""
    global _r2
    if _r2 is None:
        cfg = _get_cfg()
        if cfg is not None:
            try:
                _r2 = R2Client(cfg)
            except Exception as exc:
                logger.warning("api: R2Client not initialised: %s", exc)
    return _r2


def _get_pipeline(pipeline_id: PipelineId) -> RmsPipeline | None:
    """Return the singleton RmsPipeline for *pipeline_id*, if dependencies load."""
    cfg = _get_cfg()
    r2 = _get_r2()
    if cfg is None or r2 is None:
        return None

    pipeline = _pipelines.get(pipeline_id)
    if pipeline is None or pipeline.cfg is not cfg or pipeline.r2 is not r2:
        pipeline = RmsPipeline.for_id(pipeline_id, cfg, r2, ModelRouter(cfg))
        _pipelines[pipeline_id] = pipeline
    return pipeline


async def _run_pipeline_bg(pipeline_id: PipelineId, dry_run: bool, run_id: str) -> None:
    """
    Await a pipeline run in the FastAPI background task and clear state safely.

    Exceptions are logged and swallowed so a failed run never crashes the API
    server. The caller-generated run ID is passed through to the RunReport.
    """
    _running[pipeline_id] = True
    try:
        pipeline = _get_pipeline(pipeline_id)
        if pipeline is None:
            logger.error("api: cannot run %r - config/R2 not ready", pipeline_id)
            return
        report = await pipeline.run(dry_run=dry_run, run_id=run_id)
        logger.info(
            "api: pipeline %s finished runId=%s summary=%s error=%s",
            pipeline_id,
            report.runId,
            report.summary,
            report.error,
        )
    except Exception:
        logger.exception("api: unhandled error in pipeline %r", pipeline_id)
    finally:
        _running[pipeline_id] = False


@app.get("/health")
async def health() -> JSONResponse:
    """Return service health and per-pipeline run state."""
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "pipelines": {
                pipeline_id: ("running" if _running[pipeline_id] else "idle")
                for pipeline_id in _running
            },
        },
    )


@app.post("/rebuild/{pipeline_id}/run", status_code=202)
async def trigger_run(
    pipeline_id: Annotated[PipelineIdLiteral, Path()],
    background_tasks: BackgroundTasks,
    body: RunRequest = Body(default=RunRequest()),
) -> JSONResponse:
    """Trigger a pipeline run and return the single source-of-truth run ID."""
    typed_pipeline_id = cast(PipelineId, pipeline_id)
    if _running.get(typed_pipeline_id):
        return JSONResponse(
            status_code=409,
            content={"error": "pipeline already running", "pipeline": typed_pipeline_id},
        )

    cfg = _get_cfg()
    env_dry_run = cfg.rms_dry_run if cfg is not None else True
    dry_run = body.dry_run if body.dry_run is not None else env_dry_run
    run_id = _make_run_id()

    _running[typed_pipeline_id] = True
    background_tasks.add_task(_run_pipeline_bg, typed_pipeline_id, dry_run, run_id)

    return JSONResponse(
        status_code=202,
        content={"runId": run_id, "pipeline": typed_pipeline_id, "dryRun": dry_run},
    )


def serve() -> None:
    """Start the RMS FastAPI server using uvicorn."""
    import uvicorn

    cfg = _get_cfg()
    r2 = _get_r2()
    host = cfg.rms_host if cfg is not None else "0.0.0.0"
    port = cfg.rms_port if cfg is not None else 8000
    log_level = cfg.log_level if cfg is not None else "info"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if cfg is not None and r2 is not None:
        scheduler = build_scheduler(cfg, r2)
        scheduler.start()
        logger.info("rms-api: scheduler started with cron=%r", cfg.rms_schedule_cron)

    logger.info("rms-api: starting on %s:%d (log_level=%s)", host, port, log_level)
    uvicorn.run(
        "repo_mgmt.api:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=False,
    )
