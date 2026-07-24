"""FastAPI application for the Repo Management Suite."""

from __future__ import annotations

import asyncio
import json
import hmac
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path as FilePath
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import BackgroundTasks, Body, FastAPI, Header, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from repo_mgmt import pipeline as pipeline_mod  # noqa: F401
from repo_mgmt.config import (
    ConfigurationError,
    PipelineId,
    Settings,
    configured_worker_count,
    load_settings,
)
from repo_mgmt.git_manager import GitManager
from repo_mgmt import lifecycle
from repo_mgmt.model_router import ModelRouter
from repo_mgmt.ops_alerts import send_operational_event
from repo_mgmt.pipeline import RmsPipeline
from repo_mgmt.r2_client import R2Client, R2Error
from repo_mgmt.repo_bootstrap import BootstrapResult, bootstrap_repositories
from repo_mgmt.runtime_guard import cleanup_stale_reports

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Initialise local state and close clients within Koyeb shutdown handling."""
    global _shutting_down, _model_router, _r2_monitor_task
    _shutting_down = False
    cfg = _get_cfg()
    if cfg is None or not _usable_secret(cfg.rms_api_key):
        logger.warning(
            "rms-api: RMS_API_KEY is not set — protected endpoints require it unless RMS_ALLOW_UNAUTHENTICATED_DEV=true"
        )
    if cfg is not None and cfg.rms_temp_cleanup_enabled:
        deleted = await asyncio.to_thread(
            cleanup_stale_reports, cfg.report_dir(), cfg.rms_temp_max_age_hours
        )
        if deleted:
            logger.info("rms-api: removed %d stale local report(s)", deleted)
    if cfg is not None and _r2_configured(cfg):
        _r2_monitor_task = asyncio.create_task(_r2_monitor_loop(cfg))
    try:
        yield
    finally:
        _shutting_down = True
        if _r2_monitor_task is not None:
            _r2_monitor_task.cancel()
            try:
                await _r2_monitor_task
            except asyncio.CancelledError:
                pass
            _r2_monitor_task = None
        router = _model_router
        _model_router = None
        _pipelines.clear()
        if router is not None:
            await router.aclose()


app = FastAPI(
    title="Repo Management Suite",
    description="Autonomous repository audit and patch pipeline service.",
    version="1.1.0",
    lifespan=_lifespan,
)


@app.middleware("http")
async def production_response_headers(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Attach conservative API security headers to every response."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
    request_id = request.headers.get("X-Request-ID", "").strip()[:128]
    if request_id:
        response.headers.setdefault("X-Request-ID", request_id)
    return response

PipelineIdLiteral = Literal["seo-aeo-geo", "mobile-ux", "on-brand"]
_PIPELINE_IDS: tuple[PipelineIdLiteral, ...] = ("seo-aeo-geo", "mobile-ux", "on-brand")

_pipelines: dict[PipelineId, RmsPipeline] = {}
_running: dict[PipelineId, bool] = {pipeline_id: False for pipeline_id in _PIPELINE_IDS}
_model_router: ModelRouter | None = None
_active_pipeline: PipelineId | None = None
_active_run_id: str | None = None
_shutting_down = False
_admission_lock = threading.Lock()
_idempotency: OrderedDict[str, tuple[PipelineId, str, bool]] = OrderedDict()
_runtime_details_cache: tuple[float, dict[str, object]] | None = None

_cfg: Settings | None = None
_cfg_error: str | None = None
_r2: R2Client | None = None
_r2_error: str | None = None
_r2_verified: bool | None = None
_r2_verify_error: str | None = None
_r2_verified_at: float | None = None
_r2_last_success_at: str | None = None
_r2_last_failure_at: str | None = None
_r2_check_count = 0
_r2_monitor_task: asyncio.Task[None] | None = None
_bootstrap_attempted: bool = False
_bootstrap_results: list[BootstrapResult] = []


class RunRequest(BaseModel):
    """Optional body for POST /rebuild/{pipeline_id}/run."""

    dry_run: bool | None = None


class AdmissionError(Exception):
    """Raised when an endpoint request cannot be safely admitted."""

    def __init__(
        self, status_code: int, error: str, details: dict[str, object]
    ) -> None:
        """Initialise an admission error with an HTTP status and JSON details."""
        super().__init__(error)
        self.status_code = status_code
        self.error = error
        self.details = details


def _usable_secret(value: str | None) -> str:
    """Return a configured secret, treating unresolved Koyeb placeholders as missing."""
    text = (value or "").strip()
    if re.fullmatch(r"\{\{\s*secret\.[^}]+\}\}", text, flags=re.IGNORECASE):
        return ""
    return text


def _make_run_id() -> str:
    """Return a UTC run identifier safe for branch names and R2 keys."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _get_cfg() -> Settings | None:
    """Return cached Settings, loading lazily on first use."""
    global _cfg, _cfg_error
    if _cfg is None and _cfg_error is None:
        try:
            _cfg = load_settings()
        except ConfigurationError:
            _cfg_error = "Configuration failed to load"
            logger.exception("api: config not fully loaded")
        except Exception:
            _cfg_error = "Failed to load configuration"
            logger.exception("api: config not fully loaded")
    return _cfg


def _get_r2() -> R2Client | None:
    """Return the cached R2 client, creating it lazily when config is ready."""
    global _r2, _r2_error
    if _r2 is None and _r2_error is None:
        cfg = _get_cfg()
        if cfg is not None:
            try:
                _r2 = R2Client(cfg)
            except Exception:
                _r2_error = "R2 client initialisation failed"
                logger.exception("api: R2Client not initialised")
    return _r2


def _r2_configured(cfg: Settings | None) -> bool:
    """Return True when required R2 configuration values are present."""
    if cfg is None:
        return False
    return all(
        [
            cfg.r2_endpoint.startswith(("http://", "https://")),
            bool(_usable_secret(cfg.r2_access_key_id)),
            bool(_usable_secret(cfg.r2_secret_access_key)),
            bool(cfg.r2_bucket_audits.strip()),
        ]
    )


def _verify_r2(*, force: bool = False) -> bool:
    """Probe R2 with a bounded TTL and record non-secret verification evidence."""
    global _r2_verified, _r2_verify_error, _r2_verified_at
    global _r2_last_success_at, _r2_last_failure_at, _r2_check_count
    cfg = _get_cfg()
    now = time.monotonic()
    ttl = cfg.rms_r2_verify_interval_seconds if cfg is not None else 900
    if (
        not force
        and _r2_verified is not None
        and _r2_verified_at is not None
        and now - _r2_verified_at < ttl
    ):
        return _r2_verified
    previous = _r2_verified
    r2 = _get_r2() if cfg is not None else None
    _r2_check_count += 1
    _r2_verified_at = now
    if cfg is None or r2 is None or not _r2_configured(cfg):
        _r2_verified = False
        _r2_verify_error = "R2 is not fully configured"
    else:
        try:
            _r2_verified = bool(r2.verify_bucket(cfg.r2_bucket_audits))
            _r2_verify_error = None if _r2_verified else "R2 bucket verification returned false"
        except Exception:
            _r2_verified = False
            _r2_verify_error = "R2 verification failed"
            logger.exception("api: R2 verification failed")
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    if _r2_verified:
        _r2_last_success_at = checked_at
    else:
        _r2_last_failure_at = checked_at
        if cfg is not None and previous is not False:
            send_operational_event(
                cfg,
                {
                    "event_id": f"rams:r2-verification:{checked_at}",
                    "severity": "critical",
                    "event_type": "audit_bucket_verification_failed",
                    "title": "RAMS audit-bucket verification failed",
                    "summary": "RAMS could not verify the governed audits bucket.",
                    "release_id": cfg.rms_release_id or None,
                    "details": {"bucket": cfg.r2_bucket_audits, "checkCount": _r2_check_count},
                },
            )
    return bool(_r2_verified)


async def _r2_monitor_loop(cfg: Settings) -> None:
    """Periodically verify the governed audit bucket for the life of the service."""
    while True:
        await asyncio.to_thread(_verify_r2, force=True)
        await asyncio.sleep(cfg.rms_r2_verify_interval_seconds)


def _version_output(command: str) -> tuple[bool, str]:
    """Run a runtime binary version check and return safe public output."""
    binary = command.split()[0]
    if shutil.which(binary) is None:
        return False, f"{binary} not found"
    try:
        result = subprocess.run(
            command.split(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.exception("api: dependency version check failed for %s", binary)
        return False, f"{binary} version check failed"
    output = result.stdout.strip()
    return result.returncode == 0, output


def _node_major_ok(version_text: str) -> bool:
    """Return True when node --version reports major version 20 or newer."""
    token = version_text.strip().lstrip("v").split(".", 1)[0]
    try:
        return int(token) >= 20
    except ValueError:
        return False


def _validation_runtime_details() -> dict[str, object]:
    """Return cached public checks for validation/runtime dependencies."""
    global _runtime_details_cache
    cfg = _get_cfg()
    ttl = cfg.rms_readiness_cache_seconds if cfg is not None else 300
    now = time.monotonic()
    if _runtime_details_cache is not None and now - _runtime_details_cache[0] < ttl:
        return dict(_runtime_details_cache[1])
    python_ok, python_version = _version_output("python --version")
    git_ok, git_version = _version_output("git --version")
    node_ok, node_version = _version_output("node --version")
    npm_ok, npm_version = _version_output("npm --version")
    node_ok = node_ok and _node_major_ok(node_version)
    details: dict[str, object] = {
        "ready": python_ok and git_ok and node_ok and npm_ok,
        "python": python_version,
        "git": git_version,
        "node": node_version,
        "npm": npm_version,
    }
    _runtime_details_cache = (now, details)
    return dict(details)


def _model_router_ready(cfg: Settings | None) -> bool:
    """Return True when model-router settings are present and syntactically usable."""
    if cfg is None:
        return False
    return all(
        [
            cfg.openrouter_api_base.startswith(("http://", "https://")),
            bool(_usable_secret(cfg.openrouter_api_key)),
            bool(cfg.openrouter_primary_model.strip()),
            bool(cfg.openrouter_secondary_model.strip()),
            bool(cfg.openrouter_triage_model.strip()),
        ]
    )


def _repo_ready(path: FilePath) -> bool:
    """Return True when *path* exists and is a directory."""
    return path.exists() and path.is_dir()


def _bootstrap_target_ready_on_demand(
    cfg: Settings,
    *,
    label: str,
    repo_url: str,
) -> bool:
    """Return whether an ephemeral target can be materialised when work starts.

    Koyeb production instances intentionally keep repository worktrees in
    ephemeral storage. A missing idle worktree is therefore not a dependency
    failure when bootstrap is enabled and its target URL is resolved. A prior
    failed bootstrap remains a real degraded condition until a later attempt
    succeeds.
    """

    if not cfg.rms_repo_bootstrap_enabled or not _usable_secret(repo_url):
        return False
    result = next((item for item in _bootstrap_results if item.label == label), None)
    if result is not None and result.attempted and not result.ready:
        return False
    return True


def _ensure_repos_bootstrapped(
    cfg: Settings, pipeline_id: PipelineId | None = None
) -> list[BootstrapResult]:
    """Run repo bootstrap once per process when explicitly enabled.

    Bootstrap only the repository required by the requested pipeline. This avoids
    blocking an AIMS/on-brand run because the website repo is temporarily
    unavailable, and vice versa.
    """
    global _bootstrap_attempted, _bootstrap_results
    label = "all"
    if pipeline_id in {"seo-aeo-geo", "mobile-ux"}:
        label = "website"
    elif pipeline_id == "on-brand":
        label = "aims"
    existing = {result.label: result for result in _bootstrap_results}
    if not existing.get(
        label, BootstrapResult(label, "", False, False, "missing")
    ).ready:
        _bootstrap_attempted = True
        try:
            new_results = bootstrap_repositories(cfg, pipeline_id=pipeline_id)
            for result in new_results:
                existing[result.label] = result
            _bootstrap_results = list(existing.values())
        except Exception as exc:
            logger.exception("api: repo bootstrap failed")
            existing[label] = BootstrapResult(
                label=label,
                path="",
                attempted=True,
                ready=False,
                action="failed",
                error=str(exc),
            )
            _bootstrap_results = list(existing.values())
    return _bootstrap_results


def _bootstrap_details(cfg: Settings | None) -> dict[str, object]:
    """Return public bootstrap readiness information without secrets."""
    if cfg is None:
        return {"enabled": False, "attempted": False, "results": []}
    results = _bootstrap_results
    return {
        "enabled": cfg.rms_repo_bootstrap_enabled,
        "attempted": _bootstrap_attempted,
        "results": [result.__dict__ for result in results],
    }


def _dependency_details() -> dict[str, object]:
    """Build public dependency readiness booleans and non-secret errors."""
    cfg = _get_cfg()
    r2 = _get_r2() if cfg is not None else None
    website_ready = False
    aims_ready = False
    validation_runtime = _validation_runtime_details()
    pipeline_repo_paths: dict[str, str] = {}
    repo_materialized: dict[str, bool] = {"website": False, "aims": False}
    repo_ready_on_demand: dict[str, bool] = {"website": False, "aims": False}
    if cfg is not None:
        website_path = cfg.repo_path_for("seo-aeo-geo")
        aims_path = cfg.repo_path_for("on-brand")
        repo_materialized = {
            "website": _repo_ready(website_path),
            "aims": _repo_ready(aims_path),
        }
        repo_ready_on_demand = {
            "website": _bootstrap_target_ready_on_demand(
                cfg,
                label="website",
                repo_url=cfg.rms_website_repo_url,
            ),
            "aims": _bootstrap_target_ready_on_demand(
                cfg,
                label="aims",
                repo_url=cfg.rms_aims_repo_url,
            ),
        }
        website_ready = repo_materialized["website"] or repo_ready_on_demand["website"]
        aims_ready = repo_materialized["aims"] or repo_ready_on_demand["aims"]
        pipeline_repo_paths = {
            "seo-aeo-geo": str(website_path),
            "mobile-ux": str(cfg.repo_path_for("mobile-ux")),
            "on-brand": str(aims_path),
        }
    deps: dict[str, object] = {
        "config_loaded": cfg is not None,
        "r2_configured": _r2_configured(cfg),
        "r2_verified": _verify_r2() if cfg is not None and r2 is not None else False,
        "website_repo_ready": website_ready,
        "aims_repo_ready": aims_ready,
        "pipeline_repo_paths": pipeline_repo_paths,
        "repo_bootstrap": {
            **_bootstrap_details(cfg),
            "materialized": repo_materialized,
            "ready_on_demand": repo_ready_on_demand,
        },
        "validation_runtime_ready": bool(validation_runtime["ready"]),
        "model_router_ready": _model_router_ready(cfg),
        "single_worker_mode": configured_worker_count() == 1,
        "runtime": validation_runtime,
    }
    errors: dict[str, str] = {}
    if _cfg_error:
        errors["config"] = _cfg_error
    if _r2_error:
        errors["r2_client"] = _r2_error
    if _r2_verify_error:
        errors["r2_verification"] = _r2_verify_error
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
    """Build the exact /health contract from the RMS specification, plus lifecycle state."""
    busy = any(_running.values())
    state = lifecycle.compute_state(busy=busy, dependencies_ready=True)
    return {"status": "ok", "pipelines": _pipeline_states(), "lifecycle": state}


def _readiness_payload() -> dict[str, object]:
    """Build dependency readiness detail for deployment probes and operators."""
    deps = _dependency_details()
    ready_values = [value for value in deps.values() if isinstance(value, bool)]
    dependencies_ready = bool(ready_values) and all(ready_values)
    status = "ready" if dependencies_ready else "degraded"
    busy = any(_running.values())
    state = lifecycle.compute_state(busy=busy, dependencies_ready=dependencies_ready)
    return {
        "status": status,
        "pipelines": _pipeline_states(),
        "dependencies": deps,
        "admission": _active_status(),
        "lifecycle": state,
    }


def _get_model_router() -> ModelRouter | None:
    """Return the single reusable OpenRouter client for this eMicro process."""
    global _model_router
    cfg = _get_cfg()
    if cfg is None:
        return None
    if _model_router is None:
        _model_router = ModelRouter(cfg)
    return _model_router


def _active_status() -> dict[str, object]:
    """Return compact non-secret global admission state."""
    return {
        "acceptingRuns": not _shutting_down
        and _active_pipeline is None
        and not any(_running.values())
        and not lifecycle.is_in_maintenance(),
        "activePipeline": _active_pipeline
        or next((p for p, running in _running.items() if running), None),
        "activeRunId": _active_run_id,
        "shuttingDown": _shutting_down,
        "lifecycle": lifecycle.snapshot(),
        "maxConcurrentPipelines": (
            _cfg.rms_max_concurrent_pipelines if _cfg is not None else 1
        ),
    }


def _disk_free_mb(path: FilePath) -> int:
    """Return free disk space for the closest existing parent."""
    candidate = path.expanduser()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return int(shutil.disk_usage(candidate).free // (1024 * 1024))


def _remember_idempotency(
    key: str, value: tuple[PipelineId, str, bool], cfg: Settings
) -> None:
    if not key:
        return
    _idempotency[key] = value
    _idempotency.move_to_end(key)
    while len(_idempotency) > cfg.rms_idempotency_cache_size:
        _idempotency.popitem(last=False)


def _get_pipeline(pipeline_id: PipelineId) -> RmsPipeline | None:
    """Return the singleton RmsPipeline for *pipeline_id*, if dependencies load."""
    cfg = _get_cfg()
    r2 = _get_r2()
    if cfg is None or r2 is None:
        return None

    pipeline = _pipelines.get(pipeline_id)
    if pipeline is None or pipeline.cfg is not cfg or pipeline.r2 is not r2:
        router = _get_model_router()
        if router is None:
            return None
        pipeline = RmsPipeline.for_id(pipeline_id, cfg, r2, router)
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
    free_mb = _disk_free_mb(FilePath(cfg.rms_repo_base_dir))
    if free_mb < cfg.rms_min_free_disk_mb:
        raise AdmissionError(
            507,
            "insufficient local disk space",
            {"freeDiskMb": free_mb, "requiredFreeDiskMb": cfg.rms_min_free_disk_mb},
        )
    if _get_r2() is None or not _verify_r2():
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
    _ensure_repos_bootstrapped(cfg, pipeline_id)
    target_repo = cfg.repo_path_for(pipeline_id)
    if not _repo_ready(target_repo):
        raise AdmissionError(
            503,
            "target repo path unavailable",
            {
                "pipeline": pipeline_id,
                "repoReady": False,
                "repoPath": str(target_repo),
                "repoBootstrap": _bootstrap_details(cfg),
            },
        )

    effective_dry_run = cfg.rms_dry_run if requested is None else requested
    if effective_dry_run:
        return True

    if not cfg.live_write_permitted:
        details = cfg.live_write_gate_diagnostics(
            requested_dry_run=requested,
            effective_dry_run=effective_dry_run,
        )
        raise AdmissionError(
            403,
            "live write refused",
            {
                "reason": (
                    "Live writes require the request to resolve to dry_run=false, "
                    "RMS_DRY_RUN=false, and RMS_LIVE_WRITE_ENABLED=true. "
                    "All gates must be explicitly present and parseable."
                ),
                **details,
                # Backwards-compatible booleans retained for existing Make/log checks.
                "dryRunEnvExplicitAndParseable": cfg.dry_run_env_explicit_and_parseable,
                "liveWriteEnvExplicitAndParseable": cfg.live_write_env_explicit_and_parseable,
            },
        )

    git_mgr = GitManager(
        target_repo,
        cfg.rms_qa_branch_prefix,
        cfg.rms_push_enabled,
        cfg.rms_git_timeout_seconds,
        cfg.rms_git_output_max_bytes,
    )
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
    """Await a pipeline run and clear global eMicro admission state safely."""
    global _active_pipeline, _active_run_id
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
        with _admission_lock:
            _running[pipeline_id] = False
            if _active_run_id == run_id:
                _active_pipeline = None
                _active_run_id = None


@app.get("/")
async def root() -> JSONResponse:
    """Return the minimal public health contract for root probes."""
    return JSONResponse(status_code=200, content=_health_payload())


@app.get("/health")
async def health() -> JSONResponse:
    """Return the exact RAMS liveness contract: status plus pipeline states."""
    return JSONResponse(status_code=200, content=_health_payload())


@app.get("/livez")
async def livez() -> JSONResponse:
    """Return a Kubernetes/Koyeb-compatible liveness response."""
    return JSONResponse(status_code=200, content=_health_payload())


@app.get("/ops/warmup")
async def ops_warmup(
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Warm local configuration and HTTP pools without starting operational work."""
    auth_error = _auth_error_response(authorization)
    if auth_error is not None:
        return auth_error
    cfg = _get_cfg()
    router = _get_model_router()
    if cfg is None or router is None:
        return JSONResponse(
            status_code=503, content={"status": "degraded", **_active_status()}
        )
    client = router.warmup()
    return JSONResponse(
        status_code=200,
        content={
            "status": "warm",
            "service": "RAMS",
            "warmupScope": ["configuration", "bounded-http-clients"],
            "excludedWork": [
                "repositories",
                "R2",
                "audits",
                "validation",
                "OpenRouter requests",
            ],
            "client": client,
            **_active_status(),
        },
    )


@app.get("/ops/excellence")
async def operational_excellence(
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Return professional-operations evidence without exposing credentials."""
    auth_error = _auth_error_response(authorization)
    if auth_error is not None:
        return auth_error
    cfg = _get_cfg()
    verified = await asyncio.to_thread(_verify_r2)
    return JSONResponse(
        status_code=200 if verified else 503,
        content={
            "status": "healthy" if verified else "degraded",
            "service": "RAMS",
            "releaseId": cfg.rms_release_id if cfg else "",
            "auditStorage": {
                "verified": verified,
                "checkCount": _r2_check_count,
                "lastSuccessAt": _r2_last_success_at,
                "lastFailureAt": _r2_last_failure_at,
                "verificationIntervalSeconds": cfg.rms_r2_verify_interval_seconds if cfg else None,
                "reportRetentionDays": cfg.rms_report_retention_days if cfg else None,
                "error": _r2_verify_error,
            },
            "repositoryBootstrap": _bootstrap_details(cfg),
            "deploymentContract": {
                "target": "paid Koyeb production instance",
                "healthCheckPath": "/health",
                "webConcurrency": configured_worker_count(),
                "uvicornWorkers": os.environ.get("UVICORN_WORKERS", "1"),
                "singleWorkerMode": cfg.rms_single_worker_mode if cfg else None,
                "maxConcurrentPipelines": cfg.rms_max_concurrent_pipelines if cfg else None,
                "maxIssuesPerRun": cfg.rms_max_issues_per_run if cfg else None,
                "warmupExternalWork": False,
            },
            "liveWriteControls": {
                "dryRunDefault": cfg.rms_dry_run if cfg else None,
                "liveWriteEnabled": cfg.rms_live_write_enabled if cfg else None,
                "liveWritePermitted": cfg.live_write_permitted if cfg else False,
                "pushEnabled": cfg.rms_push_enabled if cfg else None,
                "createPr": cfg.rms_create_pr if cfg else None,
                "validateAfterEachTask": cfg.rms_validate_after_each_task if cfg else None,
                "revertOnValidationFailure": cfg.rms_revert_on_validation_failure if cfg else None,
                "meaning": (
                    "Production mode can run governed workflows and publish validated "
                    "patch/report artefacts. Pushing and PR creation remain disabled "
                    "until RMS_PUSH_ENABLED or RMS_CREATE_PR are deliberately enabled."
                ),
            },
            "modelProviderPolicy": {
                "promptLogging": cfg.rms_openrouter_log_prompts if cfg else None,
                "dataCollection": cfg.rms_openrouter_data_collection if cfg else None,
                "fallbacksEnabled": cfg.rms_openrouter_allow_fallbacks if cfg else None,
                "maxRetries": cfg.rms_openrouter_max_retries if cfg else None,
            },
            "protectedEndpoints": [
                "/readiness",
                "/readyz",
                "/ops/warmup",
                "/ops/excellence",
                "/reports/*",
                "/rebuild/{pipeline_id}/run",
            ],
            "admission": _active_status(),
        },
    )


@app.get("/readiness")
@app.get("/readyz")
async def readiness(
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Return authenticated dependency readiness detail."""
    auth_error = _auth_error_response(authorization)
    if auth_error is not None:
        return auth_error
    payload = _readiness_payload()
    status_code = 200 if payload.get("status") == "ready" else 503
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/admin/lifecycle")
async def get_lifecycle(
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Return the current self-observed lifecycle snapshot."""
    auth_error = _auth_error_response(authorization)
    if auth_error is not None:
        return auth_error
    return JSONResponse(status_code=200, content=lifecycle.snapshot())


class MaintenanceRequest(BaseModel):
    on: bool
    reason: str | None = None


@app.post("/admin/lifecycle/maintenance")
async def set_maintenance(
    request_body: MaintenanceRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Force RAMS into or out of Maintenance state (operator/MAST controlled)."""
    auth_error = _auth_error_response(authorization)
    if auth_error is not None:
        return auth_error
    if request_body.on:
        snapshot = lifecycle.enter_maintenance(reason=request_body.reason or "operator-requested")
    else:
        snapshot = lifecycle.exit_maintenance(reason=request_body.reason or "operator-cleared")
    return JSONResponse(status_code=200, content=snapshot)


def _auth_error_response(authorization: str | None) -> JSONResponse | None:
    """Return an auth error for protected endpoints, leaving only / and /health public."""
    cfg_for_auth = _get_cfg()
    if cfg_for_auth is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "configuration unavailable",
                "dependencies": _dependency_details(),
            },
        )

    expected_key = _usable_secret(cfg_for_auth.rms_api_key)
    if not expected_key:
        if cfg_for_auth.rms_allow_unauthenticated_dev:
            return None
        return JSONResponse(
            status_code=503,
            content={
                "error": "RMS_API_KEY is required for protected endpoints",
                "hint": "Set RMS_API_KEY for deployed use, or set RMS_ALLOW_UNAUTHENTICATED_DEV=true for local-only development.",
            },
        )

    token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[len("bearer ") :].strip()
    if token is not None and hmac.compare_digest(token, expected_key):
        return None
    return JSONResponse(
        status_code=401,
        content={"error": "unauthorized"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _write_auth_error_response(authorization: str | None) -> JSONResponse | None:
    """Fail closed for public rebuild endpoints unless explicitly authorised."""
    cfg_for_auth = _get_cfg()
    if cfg_for_auth is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "configuration unavailable",
                "dependencies": _dependency_details(),
            },
        )
    if not _usable_secret(cfg_for_auth.rms_api_key):
        if cfg_for_auth.rms_allow_unauthenticated_dev:
            return None
        return JSONResponse(
            status_code=503,
            content={
                "error": "RMS_API_KEY is required for rebuild endpoints",
                "hint": "Set RMS_API_KEY for deployed use, or set RMS_ALLOW_UNAUTHENTICATED_DEV=true for local-only development.",
            },
        )
    return _auth_error_response(authorization)


def _report_config_or_response() -> tuple[Settings | None, JSONResponse | None]:
    """Return settings for report endpoints, or a structured 503 response."""
    cfg = _get_cfg()
    if cfg is None:
        return None, JSONResponse(
            status_code=503,
            content={
                "error": "configuration unavailable",
                "dependencies": _dependency_details(),
            },
        )
    return cfg, None


def _safe_report_dir(cfg: Settings) -> FilePath:
    """Return the configured local report directory as a resolved path."""
    return cfg.report_dir().expanduser().resolve()


def _dry_run_report_run_id(path: FilePath, pipeline_id: PipelineId) -> str:
    """Extract the run ID from a dry-run report filename."""
    prefix = f"dry-run-{pipeline_id}-"
    suffix = "-report.json"
    name = path.name
    if name.startswith(prefix) and name.endswith(suffix):
        return name[len(prefix) : -len(suffix)]
    return ""


def _dry_run_report_records(
    cfg: Settings, pipeline_id: PipelineId
) -> list[dict[str, Any]]:
    """Return newest-first metadata for local dry-run reports for *pipeline_id*."""
    report_dir = _safe_report_dir(cfg)
    if not report_dir.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in report_dir.glob(f"dry-run-{pipeline_id}-*-report.json"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        run_id = _dry_run_report_run_id(path, pipeline_id)
        if not run_id:
            continue
        updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        records.append(
            {
                "runId": run_id,
                "filename": path.name,
                "sizeBytes": stat.st_size,
                "updatedAt": updated_at,
                "url": f"/reports/dry-run/{pipeline_id}/{run_id}",
            }
        )
    return sorted(records, key=lambda item: str(item["runId"]), reverse=True)


def _dry_run_report_path(
    cfg: Settings, pipeline_id: PipelineId, run_id: str
) -> FilePath | None:
    """Return the safe path for a local dry-run report, or None if invalid/missing."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z", run_id) is None:
        return None
    report_dir = _safe_report_dir(cfg)
    path = (report_dir / f"dry-run-{pipeline_id}-{run_id}-report.json").resolve()
    try:
        path.relative_to(report_dir)
    except ValueError:
        return None
    return path if path.is_file() else None


def _read_json_report(
    path: FilePath,
) -> tuple[dict[str, Any] | list[Any] | None, str | None]:
    """Read a local JSON report file and return payload plus optional error."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"report is not valid JSON: {exc.msg}"
    except OSError as exc:
        return None, f"report could not be read: {exc}"
    if isinstance(payload, (dict, list)):
        return payload, None
    return None, "report JSON root must be an object or array"


def _live_report_key(
    cfg: Settings, pipeline_id: PipelineId, run_id: str | None = None
) -> str:
    """Return the R2 key for a live report or latest pointer."""
    prefix = cfg.rms_report_prefix.strip("/")
    if run_id is None:
        return f"{prefix}/{pipeline_id}/latest.json"
    return f"{prefix}/{pipeline_id}/{run_id}/report.json"


def _valid_run_id(run_id: str) -> bool:
    """Return True when *run_id* has the RAMS timestamp shape."""
    return re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z", run_id) is not None


def _read_r2_json_report(
    cfg: Settings,
    pipeline_id: PipelineId,
    key: str,
) -> tuple[dict[str, Any] | list[Any] | None, str | None, int]:
    """Read a JSON report from R2 and return payload, error, and status code."""
    r2 = _get_r2()
    if r2 is None:
        return None, _r2_error or "R2 client unavailable", 503
    try:
        limited = getattr(type(r2), "get_object_limited", None)
        if callable(limited):
            raw = r2.get_object_limited(
                cfg.r2_bucket_audits, key, cfg.rms_report_max_bytes
            )
        else:
            raw = r2.get_object(cfg.r2_bucket_audits, key)
            if len(raw) > cfg.rms_report_max_bytes:
                return None, "live report exceeds configured size limit", 413
    except R2Error as exc:
        message = str(exc)
        if "NoSuchKey" in message or "404" in message or "Not Found" in message:
            return None, f"live report not found at {key}", 404
        return None, message, 502
    try:
        payload = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        return None, f"live report is not UTF-8 JSON: {exc}", 500
    except json.JSONDecodeError as exc:
        return None, f"live report is not valid JSON: {exc.msg}", 500
    if isinstance(payload, (dict, list)):
        return payload, None, 200
    return None, "live report JSON root must be an object or array", 500


@app.get("/reports/dry-run")
async def list_all_dry_run_reports(
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """List local dry-run reports retained inside the running container."""
    auth_error = _auth_error_response(authorization)
    if auth_error is not None:
        return auth_error
    cfg, error_response = _report_config_or_response()
    if error_response is not None or cfg is None:
        return error_response or JSONResponse(
            status_code=503, content={"error": "configuration unavailable"}
        )
    pipelines: dict[str, object] = {}
    for pipeline_id in _PIPELINE_IDS:
        records = _dry_run_report_records(cfg, pipeline_id)
        pipelines[pipeline_id] = {
            "count": len(records),
            "latestRunId": records[0]["runId"] if records else None,
            "latestUrl": records[0]["url"] if records else None,
        }
    return JSONResponse(
        status_code=200,
        content={
            "reportType": "dry-run",
            "reportDir": str(_safe_report_dir(cfg)),
            "pipelines": pipelines,
        },
    )


@app.get("/reports/dry-run/{pipeline_id}")
async def list_pipeline_dry_run_reports(
    pipeline_id: Annotated[PipelineIdLiteral, Path()],
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """List local dry-run report metadata for a single pipeline."""
    auth_error = _auth_error_response(authorization)
    if auth_error is not None:
        return auth_error
    cfg, error_response = _report_config_or_response()
    if error_response is not None or cfg is None:
        return error_response or JSONResponse(
            status_code=503, content={"error": "configuration unavailable"}
        )
    typed_pipeline_id: PipelineId = pipeline_id
    records = _dry_run_report_records(cfg, typed_pipeline_id)
    return JSONResponse(
        status_code=200,
        content={
            "reportType": "dry-run",
            "pipeline": typed_pipeline_id,
            "count": len(records),
            "latestRunId": records[0]["runId"] if records else None,
            "reports": records,
        },
    )


@app.get("/reports/dry-run/{pipeline_id}/latest")
async def get_latest_dry_run_report(
    pipeline_id: Annotated[PipelineIdLiteral, Path()],
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Return the newest local dry-run report for the requested pipeline."""
    auth_error = _auth_error_response(authorization)
    if auth_error is not None:
        return auth_error
    cfg, error_response = _report_config_or_response()
    if error_response is not None or cfg is None:
        return error_response or JSONResponse(
            status_code=503, content={"error": "configuration unavailable"}
        )
    typed_pipeline_id: PipelineId = pipeline_id
    records = _dry_run_report_records(cfg, typed_pipeline_id)
    if not records:
        if _running.get(typed_pipeline_id):
            return JSONResponse(
                status_code=202,
                content={
                    "status": "pending",
                    "pipeline": typed_pipeline_id,
                    "reportDir": str(_safe_report_dir(cfg)),
                    "hint": "The pipeline is still running. Retry this endpoint after the run finishes.",
                },
            )
        return JSONResponse(
            status_code=404,
            content={
                "error": "dry-run report not found",
                "pipeline": typed_pipeline_id,
                "reportDir": str(_safe_report_dir(cfg)),
                "hint": "Run the pipeline with dry_run=true, then retry this endpoint on the same running instance.",
            },
        )
    latest_run_id = str(records[0]["runId"])
    path = _dry_run_report_path(cfg, typed_pipeline_id, latest_run_id)
    if path is None:
        return JSONResponse(
            status_code=404, content={"error": "dry-run report not found"}
        )
    payload, read_error = _read_json_report(path)
    if read_error:
        return JSONResponse(
            status_code=500,
            content={
                "error": read_error,
                "pipeline": typed_pipeline_id,
                "filename": path.name,
            },
        )
    return JSONResponse(status_code=200, content=payload)


@app.get("/reports/dry-run/{pipeline_id}/{run_id}")
async def get_dry_run_report(
    pipeline_id: Annotated[PipelineIdLiteral, Path()],
    run_id: Annotated[str, Path()],
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Return a specific local dry-run report by pipeline and run ID."""
    auth_error = _auth_error_response(authorization)
    if auth_error is not None:
        return auth_error
    cfg, error_response = _report_config_or_response()
    if error_response is not None or cfg is None:
        return error_response or JSONResponse(
            status_code=503, content={"error": "configuration unavailable"}
        )
    typed_pipeline_id: PipelineId = pipeline_id
    path = _dry_run_report_path(cfg, typed_pipeline_id, run_id)
    if path is None:
        if _running.get(typed_pipeline_id):
            return JSONResponse(
                status_code=202,
                content={
                    "status": "pending",
                    "pipeline": typed_pipeline_id,
                    "runId": run_id,
                    "reportDir": str(_safe_report_dir(cfg)),
                    "hint": "The requested dry-run report is not written yet. Retry after the run finishes.",
                },
            )
        return JSONResponse(
            status_code=404,
            content={
                "error": "dry-run report not found",
                "pipeline": typed_pipeline_id,
                "runId": run_id,
                "reportDir": str(_safe_report_dir(cfg)),
            },
        )
    payload, read_error = _read_json_report(path)
    if read_error:
        return JSONResponse(
            status_code=500,
            content={
                "error": read_error,
                "pipeline": typed_pipeline_id,
                "filename": path.name,
            },
        )
    return JSONResponse(status_code=200, content=payload)


@app.get("/reports/{pipeline_id}")
async def get_live_report_index(
    pipeline_id: Annotated[PipelineIdLiteral, Path()],
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Return live R2 report locations for one pipeline."""
    auth_error = _auth_error_response(authorization)
    if auth_error is not None:
        return auth_error
    cfg, error_response = _report_config_or_response()
    if error_response is not None or cfg is None:
        return error_response or JSONResponse(
            status_code=503, content={"error": "configuration unavailable"}
        )
    typed_pipeline_id: PipelineId = pipeline_id
    return JSONResponse(
        status_code=200,
        content={
            "reportType": "live",
            "pipeline": typed_pipeline_id,
            "bucket": cfg.r2_bucket_audits,
            "latestKey": _live_report_key(cfg, typed_pipeline_id),
            "latestUrl": f"/reports/{typed_pipeline_id}/latest",
        },
    )


@app.get("/reports/{pipeline_id}/latest")
async def get_latest_live_report(
    pipeline_id: Annotated[PipelineIdLiteral, Path()],
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Return the latest live report published to R2 for the requested pipeline."""
    auth_error = _auth_error_response(authorization)
    if auth_error is not None:
        return auth_error
    cfg, error_response = _report_config_or_response()
    if error_response is not None or cfg is None:
        return error_response or JSONResponse(
            status_code=503, content={"error": "configuration unavailable"}
        )
    typed_pipeline_id: PipelineId = pipeline_id
    key = _live_report_key(cfg, typed_pipeline_id)
    payload, read_error, status_code = _read_r2_json_report(cfg, typed_pipeline_id, key)
    if read_error:
        if status_code == 404 and _running.get(typed_pipeline_id):
            return JSONResponse(
                status_code=202,
                content={
                    "status": "pending",
                    "pipeline": typed_pipeline_id,
                    "bucket": cfg.r2_bucket_audits,
                    "key": key,
                    "hint": "The pipeline is still running. Retry this endpoint after the run finishes.",
                },
            )
        return JSONResponse(
            status_code=status_code,
            content={
                "error": read_error,
                "pipeline": typed_pipeline_id,
                "bucket": cfg.r2_bucket_audits,
                "key": key,
            },
        )
    return JSONResponse(status_code=200, content=payload)


@app.get("/reports/{pipeline_id}/{run_id}")
async def get_live_report(
    pipeline_id: Annotated[PipelineIdLiteral, Path()],
    run_id: Annotated[str, Path()],
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Return a specific live report published to R2."""
    auth_error = _auth_error_response(authorization)
    if auth_error is not None:
        return auth_error
    cfg, error_response = _report_config_or_response()
    if error_response is not None or cfg is None:
        return error_response or JSONResponse(
            status_code=503, content={"error": "configuration unavailable"}
        )
    if not _valid_run_id(run_id):
        return JSONResponse(
            status_code=404,
            content={
                "error": "live report not found",
                "pipeline": pipeline_id,
                "runId": run_id,
            },
        )
    typed_pipeline_id: PipelineId = pipeline_id
    key = _live_report_key(cfg, typed_pipeline_id, run_id)
    payload, read_error, status_code = _read_r2_json_report(cfg, typed_pipeline_id, key)
    if read_error:
        if status_code == 404 and _running.get(typed_pipeline_id):
            return JSONResponse(
                status_code=202,
                content={
                    "status": "pending",
                    "pipeline": typed_pipeline_id,
                    "runId": run_id,
                    "bucket": cfg.r2_bucket_audits,
                    "key": key,
                    "hint": "The requested live report is not written yet. Retry after the run finishes.",
                },
            )
        return JSONResponse(
            status_code=status_code,
            content={
                "error": read_error,
                "pipeline": typed_pipeline_id,
                "runId": run_id,
                "bucket": cfg.r2_bucket_audits,
                "key": key,
            },
        )
    return JSONResponse(status_code=200, content=payload)


@app.post("/rebuild/{pipeline_id}/run", status_code=202)
async def trigger_run(
    pipeline_id: Annotated[PipelineIdLiteral, Path()],
    background_tasks: BackgroundTasks,
    body: RunRequest = Body(default=RunRequest()),
    authorization: Annotated[str | None, Header()] = None,
    x_idempotency_key: Annotated[str | None, Header(alias="X-Idempotency-Key")] = None,
    x_trigger_run_key: Annotated[str | None, Header(alias="X-Trigger-Run-Key")] = None,
) -> JSONResponse:
    """Admit at most one heavyweight RAMS pipeline across all pipeline IDs."""
    global _active_pipeline, _active_run_id
    auth_error = _write_auth_error_response(authorization)
    if auth_error is not None:
        return auth_error

    typed_pipeline_id: PipelineId = pipeline_id
    cfg = _get_cfg()
    if cfg is None:
        return JSONResponse(
            status_code=503, content={"error": "configuration unavailable"}
        )
    idem_key = (x_idempotency_key or x_trigger_run_key or "").strip()[:256]
    if lifecycle.is_in_maintenance():
        return JSONResponse(
            status_code=503,
            content={
                "error": "service-in-maintenance",
                "lifecycle": lifecycle.snapshot(),
                "hint": "RAMS is intentionally in Maintenance and is not accepting new runs.",
            },
        )
    with _admission_lock:
        if idem_key and idem_key in _idempotency:
            saved_pipeline, saved_run_id, saved_dry_run = _idempotency[idem_key]
            if saved_pipeline != typed_pipeline_id:
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "idempotency key belongs to another pipeline",
                        "pipeline": saved_pipeline,
                    },
                )
            return JSONResponse(
                status_code=202,
                content={
                    "runId": saved_run_id,
                    "pipeline": saved_pipeline,
                    "dryRun": saved_dry_run,
                    "idempotentReplay": True,
                },
                headers={"X-Run-Id": saved_run_id},
            )
        running_pipeline = _active_pipeline or next(
            (p for p, running in _running.items() if running), None
        )
        if _shutting_down:
            return JSONResponse(
                status_code=503, content={"error": "service shutting down"}
            )
        if running_pipeline is not None:
            same = running_pipeline == typed_pipeline_id
            status_code = 409 if same else 429
            return JSONResponse(
                status_code=status_code,
                content={
                    "error": "pipeline already running" if same else "RAMS is busy",
                    "pipeline": typed_pipeline_id,
                    "activePipeline": running_pipeline,
                    "activeRunId": _active_run_id,
                },
                headers={"Retry-After": str(cfg.rms_busy_retry_after_seconds)},
            )
        run_id = _make_run_id()
        _active_pipeline = typed_pipeline_id
        _active_run_id = run_id
        _running[typed_pipeline_id] = True

    try:
        dry_run = await asyncio.to_thread(
            _admit_request, typed_pipeline_id, body.dry_run
        )
    except AdmissionError as exc:
        with _admission_lock:
            _running[typed_pipeline_id] = False
            if _active_run_id == run_id:
                _active_pipeline = None
                _active_run_id = None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, **exc.details},
        )
    except Exception as exc:
        with _admission_lock:
            _running[typed_pipeline_id] = False
            if _active_run_id == run_id:
                _active_pipeline = None
                _active_run_id = None
        logger.exception("api: admission failed")
        return JSONResponse(
            status_code=503, content={"error": f"admission failed: {exc}"}
        )

    with _admission_lock:
        _remember_idempotency(idem_key, (typed_pipeline_id, run_id, dry_run), cfg)
    background_tasks.add_task(_run_pipeline_bg, typed_pipeline_id, dry_run, run_id)

    return JSONResponse(
        status_code=202,
        content={"runId": run_id, "pipeline": typed_pipeline_id, "dryRun": dry_run},
        headers={"X-Run-Id": run_id},
    )


def serve() -> None:
    """Start the RMS FastAPI server using uvicorn."""
    import uvicorn

    cfg = _get_cfg()
    host = cfg.rms_host if cfg is not None else "0.0.0.0"
    port_raw = os.getenv("RMS_PORT") or os.getenv("PORT")
    try:
        port = (
            int(port_raw) if port_raw else (cfg.rms_port if cfg is not None else 8000)
        )
    except ValueError:
        logger.warning("rms-api: invalid port %r; falling back to 8000", port_raw)
        port = 8000
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
        workers=1,
        timeout_graceful_shutdown=(
            cfg.rms_shutdown_grace_seconds if cfg is not None else 25
        ),
    )
