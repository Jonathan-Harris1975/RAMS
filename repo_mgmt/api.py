"""
FastAPI application for the Repo Management Suite.

Routes:
  GET  /health
  POST /rebuild/{pipeline_id}/run

PipelineId validation:
  Must be one of: seo-aeo-geo, mobile-ux, on-brand.
  Unknown values produce a 422 Unprocessable Entity (FastAPI path validation),
  not a 404.

Run behaviour:
  - Accepts optional JSON body { "dry_run": bool }.
  - If omitted, RMS_DRY_RUN env default is used.
  - Returns 202 on acceptance, 409 if the same pipeline is already running.
  - Background task sets _running[pipeline_id], runs the pipeline, then clears it.
  - API runId and report runId are identical.
  - Exceptions in the background task are caught and logged — server never crashes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import BackgroundTasks, FastAPI, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Repo Management Suite",
    description="Autonomous repository audit and patch pipeline service.",
    version="1.0.0",
)

# In-memory run-state (True while a pipeline is executing)
_running: dict[str, bool] = {
    "seo-aeo-geo": False,
    "mobile-ux": False,
    "on-brand": False,
}

# Validated pipeline type — FastAPI will return 422 for unknown values
PipelineIdLiteral = Literal["seo-aeo-geo", "mobile-ux", "on-brand"]


# ── Request / response models ──────────────────────────────────────────────


class RunRequest(BaseModel):
    """Optional body for POST /rebuild/{pipeline_id}/run."""
    dry_run: bool | None = None


class RunAccepted(BaseModel):
    """202 Accepted response."""
    runId: str
    pipeline: str
    dryRun: bool


class HealthResponse(BaseModel):
    """GET /health response."""
    status: str
    pipelines: dict[str, str]


# ── Startup helpers ────────────────────────────────────────────────────────

_cfg = None
_r2 = None
_model_router = None


def _get_cfg():
    """Return the cached Settings, loading lazily on first call."""
    global _cfg
    if _cfg is None:
        try:
            from repo_mgmt.config import load_settings
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
                from repo_mgmt.r2_client import R2Client
                _r2 = R2Client(cfg)
            except Exception as exc:
                logger.warning("api: R2Client init failed: %s", exc)
    return _r2


def _get_model_router():
    """Return the cached ModelRouter, creating lazily on first call."""
    global _model_router
    if _model_router is None:
        cfg = _get_cfg()
        if cfg is not None:
            try:
                from repo_mgmt.model_router import ModelRouter
                _model_router = ModelRouter(cfg)
            except Exception as exc:
                logger.warning("api: ModelRouter init failed: %s", exc)
    return _model_router


# ── Background runner ──────────────────────────────────────────────────────


async def _run_pipeline(
    pipeline_id: str, dry_run: bool, run_id: str
) -> None:
    """
    Background coroutine that executes one full pipeline run.

    Sets _running[pipeline_id]=True on entry, clears it in a finally block.
    Never propagates exceptions to the caller — all errors are logged.
    """
    _running[pipeline_id] = True
    try:
        from repo_mgmt.pipeline import RmsPipeline
        cfg = _get_cfg()
        r2 = _get_r2()
        router = _get_model_router()

        if cfg is None or r2 is None or router is None:
            logger.error(
                "api: cannot run pipeline %r — dependencies not initialised", pipeline_id
            )
            return

        pipeline = RmsPipeline.for_id(pipeline_id, cfg, r2, router)  # type: ignore[arg-type]
        await pipeline.run(dry_run=dry_run, run_id=run_id)

    except Exception:
        logger.exception("api: unhandled exception in pipeline %r run %s", pipeline_id, run_id)
    finally:
        _running[pipeline_id] = False


# ── Routes ─────────────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """
    Return service health and per-pipeline run state.

    Returns:
        HealthResponse with status='ok' and per-pipeline state strings.
    """
    return HealthResponse(
        status="ok",
        pipelines={pid: ("running" if _running[pid] else "idle") for pid in _running},
    )


@app.post("/rebuild/{pipeline_id}/run", status_code=202)
async def trigger_run(
    pipeline_id: Annotated[PipelineIdLiteral, Path()],
    body: RunRequest | None = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> JSONResponse:
    """
    Trigger a pipeline run.

    Args:
        pipeline_id: One of seo-aeo-geo, mobile-ux, on-brand (validated by FastAPI).
        body: Optional JSON body with dry_run override.

    Returns:
        202 Accepted with runId, pipeline, dryRun fields.
        409 Conflict if the pipeline is already running.
    """
    if _running.get(pipeline_id):
        return JSONResponse(
            status_code=409,
            content={
                "error": "pipeline already running",
                "pipeline": pipeline_id,
            },
        )

    # Resolve dry_run: explicit body > env default
    cfg = _get_cfg()
    env_dry_run: bool = cfg.rms_dry_run if cfg is not None else True
    dry_run: bool = body.dry_run if (body is not None and body.dry_run is not None) else env_dry_run

    run_id = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

    background_tasks.add_task(_run_pipeline, pipeline_id, dry_run, run_id)

    return JSONResponse(
        status_code=202,
        content={
            "runId": run_id,
            "pipeline": pipeline_id,
            "dryRun": dry_run,
        },
    )
