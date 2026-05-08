"""
FastAPI application for the Repo Management Suite.

Routes:
  GET  /health
  POST /rebuild/{pipeline_id}/run

PipelineId validation:
  Must be one of: seo-aeo-geo, mobile-ux, on-brand.
  Unknown values produce a 422 Unprocessable Entity (FastAPI path validation).

Run behaviour:
  - Accepts optional JSON body { "dry_run": bool }.
  - Returns 202 on acceptance, 409 if the same pipeline is already running.
  - _running[pipeline_id] is set True synchronously BEFORE the background task
    is queued, closing the race window where two concurrent requests could both
    see False and both return 202.
  - Background task calls pipeline_mod.run() and clears _running in a finally block.

Entry point:
  serve() — called by the rms-api console script.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import BackgroundTasks, FastAPI, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from repo_mgmt import pipeline as pipeline_mod
from repo_mgmt.config import load_settings
from repo_mgmt.r2_client import R2Client
from repo_mgmt.scheduler import build_scheduler

logger = logging.getLogger(__name__)

# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Repo Management Suite",
    description="Autonomous repository audit and patch pipeline service.",
    version="1.0.0",
)

# In-memory run-state.  Set synchronously before queuing background task.
_running: dict[str, bool] = {
    "seo-aeo-geo": False,
    "mobile-ux": False,
    "on-brand": False,
}

PipelineIdLiteral = Literal["seo-aeo-geo", "mobile-ux", "on-brand"]


# ── Request / response models ──────────────────────────────────────────────


class RunRequest(BaseModel):
    """Optional body for POST /rebuild/{pipeline_id}/run."""
    dry_run: bool | None = None


# ── Lazy singletons ────────────────────────────────────────────────────────

_cfg = None
_r2 = None


def _get_cfg():
    """Return the cached Settings, loading lazily on first call."""
    global _cfg
    if _cfg is None:
        try:
            _cfg = load_settings()
        except Exception as exc:
            logger.warning("api: config not fully loaded: %s", exc)
    return _cfg


def _get_r2():
    """Return the cached R2Client, creating lazily on first call."""
    global _r2
    if _r2 is None:
        cfg = _get_cfg()
        if cfg is not None:
            try:
                _r2 = R2Client(cfg)
            except Exception as exc:
                logger.warning("api: R2Client not initialised: %s", exc)
    return _r2


# ── Background runner ──────────────────────────────────────────────────────


def _run_pipeline_bg(pipeline_id: str, dry_run: bool) -> None:
    """
    Background function that calls pipeline_mod.run() and clears _running.

    _running[pipeline_id] must already be True before this runs.
    Clears it in a finally block regardless of outcome.
    """
    try:
        cfg = _get_cfg()
        r2 = _get_r2()
        if cfg is None or r2 is None:
            logger.error("api: cannot run %r — config/R2 not ready", pipeline_id)
            return
        pipeline_mod.run(pipeline_id, cfg, r2, dry_run=dry_run)  # type: ignore[arg-type]
    except Exception:
        logger.exception("api: unhandled error in pipeline %r", pipeline_id)
    finally:
        _running[pipeline_id] = False


# ── Routes ─────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> JSONResponse:
    """Return service health and per-pipeline run state."""
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "pipelines": {
                pid: ("running" if _running[pid] else "idle")
                for pid in _running
            },
        },
    )


@app.post("/rebuild/{pipeline_id}/run", status_code=202)
async def trigger_run(
    pipeline_id: Annotated[PipelineIdLiteral, Path()],
    body: RunRequest | None = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> JSONResponse:
    """
    Trigger a pipeline run.

    Returns:
        202 Accepted: {"runId": ..., "pipeline": ..., "dryRun": ...}
        409 Conflict: {"error": "pipeline already running", "pipeline": ...}
        422 Unprocessable Entity: unknown pipeline_id (handled by FastAPI).
    """
    # Race-safe: check and set synchronously before queuing background work.
    if _running.get(pipeline_id):
        return JSONResponse(
            status_code=409,
            content={"error": "pipeline already running", "pipeline": pipeline_id},
        )

    cfg = _get_cfg()
    env_dry_run: bool = cfg.rms_dry_run if cfg is not None else True
    dry_run: bool = (
        body.dry_run
        if (body is not None and body.dry_run is not None)
        else env_dry_run
    )

    run_id = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

    # Set flag BEFORE queuing — closes the concurrency race window.
    _running[pipeline_id] = True
    background_tasks.add_task(_run_pipeline_bg, pipeline_id, dry_run)

    return JSONResponse(
        status_code=202,
        content={"runId": run_id, "pipeline": pipeline_id, "dryRun": dry_run},
    )


# ── Entry point ────────────────────────────────────────────────────────────


def serve() -> None:
    """
    Start the RMS FastAPI server using uvicorn.

    Called by the rms-api console script:
      rms-api = repo_mgmt.api:serve
    """
    import uvicorn

    cfg = _get_cfg()
    r2 = _get_r2()
    host: str = cfg.rms_host if cfg is not None else "0.0.0.0"
    port: int = cfg.rms_port if cfg is not None else 8000
    log_level: str = cfg.log_level if cfg is not None else "info"

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
