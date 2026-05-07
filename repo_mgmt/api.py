"""
FastAPI application for the Repo Management Suite.

Exposes three independent pipeline endpoints plus a health check.
Each pipeline run is dispatched to a background thread so the endpoint
returns immediately with 202 Accepted.

Routes:
  POST /rebuild/seo-aeo-geo/run
  POST /rebuild/mobile-ux/run
  POST /rebuild/on-brand/run
  GET  /health
"""

from __future__ import annotations

import logging
import threading
from typing import Annotated

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from repo_mgmt import pipeline as pipeline_mod
from repo_mgmt.config import PipelineId, Settings, load_settings
from repo_mgmt.r2_client import R2Client
from repo_mgmt.scheduler import build_scheduler

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Repo Management Suite",
    description="Autonomous repository audit and patch pipeline service.",
    version="1.0.0",
)

# Dependency instances — populated at startup
_cfg: Settings | None = None
_r2: R2Client | None = None


# ── Request / response models ──────────────────────────────────────────────

class RunRequest(BaseModel):
    """Optional body for POST /rebuild/<pipeline>/run."""
    dry_run: bool | None = None  # overrides env default if provided


class RunAccepted(BaseModel):
    """202 Accepted response."""
    runId: str
    pipeline: str
    dryRun: bool


class HealthResponse(BaseModel):
    """GET /health response."""
    status: str
    pipelines: dict[str, str]


# ── Lifespan / startup ─────────────────────────────────────────────────────

@app.on_event("startup")
def _startup() -> None:
    global _cfg, _r2
    _cfg = load_settings()
    _r2 = R2Client(_cfg)
    scheduler = build_scheduler(_cfg, _r2)
    scheduler.start()
    logger.info("api: startup complete — scheduler running")


# ── Helpers ────────────────────────────────────────────────────────────────

def _get_deps() -> tuple[Settings, R2Client]:
    if _cfg is None or _r2 is None:
        raise RuntimeError("API not yet initialised")
    return _cfg, _r2


def _trigger(pipeline_id: PipelineId, dry_run: bool | None) -> RunAccepted:
    """
    Check for concurrency conflicts, then dispatch the pipeline to a background
    thread and return 202 Accepted immediately.
    """
    cfg, r2 = _get_deps()

    if pipeline_mod.is_running(pipeline_id):
        raise HTTPException(
            status_code=409,
            detail={"error": "pipeline already running", "pipeline": pipeline_id},
        )

    effective_dry_run = dry_run if dry_run is not None else cfg.rms_dry_run
    run_id = pipeline_mod.report_writer.make_run_id()

    def _background() -> None:
        pipeline_mod.run(pipeline_id, cfg, r2, dry_run=effective_dry_run)

    thread = threading.Thread(target=_background, daemon=True, name=f"rms-{pipeline_id}")
    thread.start()

    logger.info(
        "api: dispatched pipeline %r (run_id=%s dry_run=%s)",
        pipeline_id,
        run_id,
        effective_dry_run,
    )
    return RunAccepted(runId=run_id, pipeline=pipeline_id, dryRun=effective_dry_run)


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.post(
    "/rebuild/seo-aeo-geo/run",
    status_code=202,
    response_model=RunAccepted,
    summary="Trigger SEO / AEO / GEO pipeline",
)
def run_seo(body: RunRequest = RunRequest()) -> RunAccepted:
    return _trigger("seo-aeo-geo", body.dry_run)


@app.post(
    "/rebuild/mobile-ux/run",
    status_code=202,
    response_model=RunAccepted,
    summary="Trigger Mobile UX pipeline",
)
def run_mobile_ux(body: RunRequest = RunRequest()) -> RunAccepted:
    return _trigger("mobile-ux", body.dry_run)


@app.post(
    "/rebuild/on-brand/run",
    status_code=202,
    response_model=RunAccepted,
    summary="Trigger On-Brand pipeline",
)
def run_on_brand(body: RunRequest = RunRequest()) -> RunAccepted:
    return _trigger("on-brand", body.dry_run)


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check — all pipelines",
)
def health() -> HealthResponse:
    pipeline_statuses = {
        pid: ("running" if pipeline_mod.is_running(pid) else "idle")  # type: ignore[arg-type]
        for pid in ["seo-aeo-geo", "mobile-ux", "on-brand"]
    }
    return HealthResponse(status="ok", pipelines=pipeline_statuses)


# ── CLI entry-point ────────────────────────────────────────────────────────

def serve() -> None:
    """Start the uvicorn server. Called by the rms-api script."""
    cfg = load_settings()
    uvicorn.run(
        "repo_mgmt.api:app",
        host=cfg.rms_host,
        port=cfg.rms_port,
        log_level=cfg.log_level.lower(),
        reload=False,
    )
