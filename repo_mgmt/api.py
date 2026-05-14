"""FastAPI application for the Repo Management Suite."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path as FilePath
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Body, FastAPI, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from repo_mgmt import pipeline as pipeline_mod  # noqa: F401
from repo_mgmt.config import (
    ConfigurationError,
    PipelineId,
    Settings,
    configured_worker_count,
    load_settings,
)
from repo_mgmt.git_manager import GitManager
from repo_mgmt.model_router import ModelRouter
from repo_mgmt.pipeline import RmsPipeline
from repo_mgmt.r2_client import R2Client

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
_cfg_error: str | None = None
_r2: R2Client | None = None
_r2_error: str | None = None


class RunRequest(BaseModel):
    """Optional body for POST /rebuild/{pipeline_id}/run."""

    dry_run: bool | None = None


class AdmissionError(Exception):
    """Raised when an endpoint request cannot be safely admitted."""

    def __init__(self, status_code: int, error: str, details: dict[str, object]) -> None:
        """Initialise an admission error with an HTTP status and JSON details."""
        super().__init__(error)
        self.status_code = status_code
        self.error = error
        self.details = details


def _make_run_id() -> str:
    """Return a UTC run identifier safe for branch names and R2 keys."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _get_cfg() -> Settings | None:
    """Return cached Settings, loading lazily on first use."""
    global _cfg, _cfg_error
    if _cfg is None and _cfg_error is None:
        try:
            _cfg = load_settings()
        except ConfigurationError as exc:
            _cfg_error = str(exc)
            logger.warning("api: config not fully loaded: %s", exc)
        except Exception as exc:
            _cfg_error = f"Failed to load configuration: {exc}"
            logger.warning("api: config not fully loaded: %s", exc)
    return _cfg


def _get_r2() -> R2Client | None:
    """Return the cached R2 client, creating it lazily when config is ready."""
    global _r2, _r2_error
    if _r2 is None and _r2_error is None:
        cfg = _get_cfg()
        if cfg is not None:
            try:
                _r2 = R2Client(cfg)
            except Exception as exc:
                _r2_error = str(exc)
                logger.warning("api: R2Client not initialised: %s", exc)
    return _r2


def _model_router_ready(cfg: Settings | None) -> bool:
    """Return True when model-router settings are present and syntactically usable."""
    if cfg is None:
        return False
    return all(
        [
            cfg.openrouter_api_base.startswith(("http://", "https://")),
            bool(cfg.openrouter_api_key.strip()),
            bool(cfg.openrouter_primary_model.strip()),
            bool(cfg.openrouter_secondary_model.strip()),
            bool(cfg.openrouter_triage_model.strip()),
        ]
    )


def _repo_ready(path: FilePath) -> bool:
    """Return True when *path* exists and is a directory."""
    return path.exists() and path.is_dir()


def _dependency_details() -> dict[str, object]:
    """Build public dependency readiness booleans and non-secret errors."""
    cfg = _get_cfg()
    r2 = _get_r2() if cfg is not None else None
    seo_ready = False
    website_ready = False
    if cfg is not None:
        seo_ready = _repo_ready(cfg.repo_path_for("seo-aeo-geo"))
        website_ready = _repo_ready(cfg.repo_path_for("on-brand"))
    deps: dict[str, object] = {
        "config_loaded": cfg is not None,
        "r2_ready": r2 is not None,
        "model_router_ready": _model_router_ready(cfg),
        "seo_repo_ready": seo_ready,
        "website_repo_ready": website_ready,
        "single_worker_mode": configured_worker_count() == 1,
    }
    errors: dict[str, str] = {}
    if _cfg_error:
        errors["config"] = _cfg_error
    if _r2_error:
        errors["r2"] = _r2_error
    if errors:
        deps["errors"] = errors
    return deps


def _pipeline_states() -> dict[str, str]:
    """Return exact public idle/running state for every pipeline."""
    return {
        pipeline_id: ("running" if _running[pipeline_id] else "idle")
        for pipeline_id in _running
    }


def _health_payload() -> dict[str, object]:
    """Build the exact /health contract from the RMS specification."""
    return {"status": "ok", "pipelines": _pipeline_states()}


def _readiness_payload() -> dict[str, object]:
    """Build dependency readiness detail for deployment probes and operators."""
    deps = _dependency_details()
    ready_values = [value for value in deps.values() if isinstance(value, bool)]
    status = "ready" if ready_values and all(ready_values) else "degraded"
    return {"status": status, "pipelines": _pipeline_states(), "dependencies": deps}


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


def _admit_request(pipeline_id: PipelineId, requested: bool | None) -> bool:
    """Return effective dry-run value or raise an AdmissionError."""
    cfg = _get_cfg()
    if cfg is None:
        raise AdmissionError(
            503,
            "configuration unavailable",
            {"dependencies": _dependency_details()},
        )
    if _get_r2() is None:
        raise AdmissionError(
            503,
            "R2 unavailable",
            {"dependencies": _dependency_details()},
        )
    if not _model_router_ready(cfg):
        raise AdmissionError(
            503,
            "model-router configuration unavailable",
            {"dependencies": _dependency_details()},
        )
    target_repo = cfg.repo_path_for(pipeline_id)
    if not _repo_ready(target_repo):
        raise AdmissionError(
            503,
            "target repo path unavailable",
            {"pipeline": pipeline_id, "repoReady": False},
        )

    effective_dry_run = cfg.rms_dry_run if requested is None else requested
    if effective_dry_run:
        return True

    if not cfg.live_write_permitted:
        raise AdmissionError(
            403,
            "live write refused",
            {
                "reason": (
                    "RMS_DRY_RUN=false and RMS_LIVE_WRITE_ENABLED=true must both "
                    "be explicitly present and parseable"
                ),
                "dryRunEnvExplicitAndParseable": cfg.dry_run_env_explicit_and_parseable,
                "liveWriteEnvExplicitAndParseable": cfg.live_write_env_explicit_and_parseable,
            },
        )

    git_mgr = GitManager(target_repo, cfg.rms_qa_branch_prefix, cfg.rms_push_enabled)
    if not git_mgr.is_git_repo():
        raise AdmissionError(
            503,
            "target repo is not a git worktree",
            {"pipeline": pipeline_id, "repoReady": False},
        )
    if not git_mgr.is_worktree_clean():
        raise AdmissionError(
            409,
            "target repo is dirty",
            {"pipeline": pipeline_id, "dirty": True},
        )
    if cfg.rms_single_worker_mode and configured_worker_count() != 1:
        raise AdmissionError(
            409,
            "single-worker deployment required",
            {"configuredWorkerCount": configured_worker_count()},
        )
    return False


async def _run_pipeline_bg(pipeline_id: PipelineId, dry_run: bool, run_id: str) -> None:
    """Await a pipeline run and clear state safely."""
    _running[pipeline_id] = True
    try:
        pipeline = _get_pipeline(pipeline_id)
        if pipeline is None:
            logger.error("api: cannot run %r - dependencies not ready", pipeline_id)
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


@app.get("/")
async def root() -> JSONResponse:
    """Return the minimal public health contract for root probes."""
    return JSONResponse(status_code=200, content=_health_payload())


@app.get("/health")
async def health() -> JSONResponse:
    """Return the exact RMS health contract: status plus pipeline states."""
    return JSONResponse(status_code=200, content=_health_payload())


@app.get("/readiness")
async def readiness() -> JSONResponse:
    """Return dependency readiness details without changing /health."""
    return JSONResponse(status_code=200, content=_readiness_payload())


@app.post("/rebuild/{pipeline_id}/run", status_code=202)
async def trigger_run(
    pipeline_id: Annotated[PipelineIdLiteral, Path()],
    background_tasks: BackgroundTasks,
    body: RunRequest = Body(default=RunRequest()),
) -> JSONResponse:
    """Trigger a pipeline run and return the single source-of-truth run ID."""
    typed_pipeline_id: PipelineId = pipeline_id
    if _running.get(typed_pipeline_id):
        return JSONResponse(
            status_code=409,
            content={
                "error": "pipeline already running",
                "pipeline": typed_pipeline_id,
            },
        )

    try:
        dry_run = _admit_request(typed_pipeline_id, body.dry_run)
    except AdmissionError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, **exc.details},
        )

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
    host = cfg.rms_host if cfg is not None else "0.0.0.0"
    port = cfg.rms_port if cfg is not None else 8000
    log_level = cfg.log_level if cfg is not None else "info"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    logger.info("rms-api: external triggering enabled; no in-process cron scheduler")
    logger.info("rms-api: starting on %s:%d (log_level=%s)", host, port, log_level)
    uvicorn.run(
        "repo_mgmt.api:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=False,
    )
