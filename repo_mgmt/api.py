"""FastAPI application for the Repo Management Suite."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path as FilePath
from typing import Annotated, Any, Literal

from fastapi import BackgroundTasks, Body, FastAPI, Header, Path
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
from repo_mgmt.repo_bootstrap import BootstrapResult, bootstrap_repositories

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Repo Management Suite",
    description="Autonomous repository audit and patch pipeline service.",
    version="1.0.0",
)


@app.on_event("startup")
async def _startup_checks() -> None:
    """Log a warning when API key authentication is not configured."""
    cfg = _get_cfg()
    if cfg is None or not cfg.rms_api_key:
        logger.warning(
            "rms-api: RMS_API_KEY is not set — trigger endpoints are unauthenticated"
        )

PipelineIdLiteral = Literal["seo-aeo-geo", "mobile-ux", "on-brand"]
_PIPELINE_IDS: tuple[PipelineIdLiteral, ...] = ("seo-aeo-geo", "mobile-ux", "on-brand")

_pipelines: dict[PipelineId, RmsPipeline] = {}
_running: dict[PipelineId, bool] = {pipeline_id: False for pipeline_id in _PIPELINE_IDS}

_cfg: Settings | None = None
_cfg_error: str | None = None
_r2: R2Client | None = None
_r2_error: str | None = None
_r2_verified: bool | None = None
_r2_verify_error: str | None = None
_bootstrap_attempted: bool = False
_bootstrap_results: list[BootstrapResult] = []


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


def _r2_configured(cfg: Settings | None) -> bool:
    """Return True when required R2 configuration values are present."""
    if cfg is None:
        return False
    return all(
        [
            cfg.r2_endpoint.startswith(("http://", "https://")),
            bool(cfg.r2_access_key_id.strip()),
            bool(cfg.r2_secret_access_key.strip()),
            bool(cfg.r2_bucket_audits.strip()),
        ]
    )


def _verify_r2() -> bool:
    """Probe R2 once and cache the verified/not-verified result."""
    global _r2_verified, _r2_verify_error
    if _r2_verified is not None:
        return _r2_verified
    cfg = _get_cfg()
    r2 = _get_r2() if cfg is not None else None
    if cfg is None or r2 is None or not _r2_configured(cfg):
        _r2_verified = False
        return False
    try:
        _r2_verified = bool(r2.verify_bucket(cfg.r2_bucket_audits))
    except Exception as exc:
        _r2_verified = False
        _r2_verify_error = str(exc)
        logger.warning("api: R2 verification failed: %s", exc)
    return _r2_verified


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
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
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
    """Return public version checks for validation/runtime dependencies."""
    python_ok, python_version = _version_output("python --version")
    git_ok, git_version = _version_output("git --version")
    node_ok, node_version = _version_output("node --version")
    npm_ok, npm_version = _version_output("npm --version")
    node_ok = node_ok and _node_major_ok(node_version)
    return {
        "ready": python_ok and git_ok and node_ok and npm_ok,
        "python": python_version,
        "git": git_version,
        "node": node_version,
        "npm": npm_version,
    }


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


def _ensure_repos_bootstrapped(cfg: Settings, pipeline_id: PipelineId | None = None) -> list[BootstrapResult]:
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
    if not existing.get(label, BootstrapResult(label, "", False, False, "missing")).ready:
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
    if cfg is not None:
        website_path = cfg.repo_path_for("seo-aeo-geo")
        aims_path = cfg.repo_path_for("on-brand")
        website_ready = _repo_ready(website_path)
        aims_ready = _repo_ready(aims_path)
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
        "repo_bootstrap": _bootstrap_details(cfg),
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




def _auth_error_response(authorization: str | None) -> JSONResponse | None:
    """Return a 401 response when optional bearer-token auth is enabled and fails."""
    cfg_for_auth = _get_cfg()
    expected_key = cfg_for_auth.rms_api_key if cfg_for_auth is not None else None
    if not expected_key:
        return None
    token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[len("bearer "):].strip()
    if token == expected_key:
        return None
    return JSONResponse(
        status_code=401,
        content={"error": "unauthorized"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _report_config_or_response() -> tuple[Settings | None, JSONResponse | None]:
    """Return settings for report endpoints, or a structured 503 response."""
    cfg = _get_cfg()
    if cfg is None:
        return None, JSONResponse(
            status_code=503,
            content={"error": "configuration unavailable", "dependencies": _dependency_details()},
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


def _dry_run_report_records(cfg: Settings, pipeline_id: PipelineId) -> list[dict[str, Any]]:
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


def _dry_run_report_path(cfg: Settings, pipeline_id: PipelineId, run_id: str) -> FilePath | None:
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


def _read_json_report(path: FilePath) -> tuple[dict[str, Any] | list[Any] | None, str | None]:
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
        return error_response or JSONResponse(status_code=503, content={"error": "configuration unavailable"})
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
        return error_response or JSONResponse(status_code=503, content={"error": "configuration unavailable"})
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
        return error_response or JSONResponse(status_code=503, content={"error": "configuration unavailable"})
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
        return JSONResponse(status_code=404, content={"error": "dry-run report not found"})
    payload, read_error = _read_json_report(path)
    if read_error:
        return JSONResponse(
            status_code=500,
            content={"error": read_error, "pipeline": typed_pipeline_id, "filename": path.name},
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
        return error_response or JSONResponse(status_code=503, content={"error": "configuration unavailable"})
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
            content={"error": read_error, "pipeline": typed_pipeline_id, "filename": path.name},
        )
    return JSONResponse(status_code=200, content=payload)


@app.post("/rebuild/{pipeline_id}/run", status_code=202)
async def trigger_run(
    pipeline_id: Annotated[PipelineIdLiteral, Path()],
    background_tasks: BackgroundTasks,
    body: RunRequest = Body(default=RunRequest()),
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Trigger a pipeline run and return the single source-of-truth run ID."""
    auth_error = _auth_error_response(authorization)
    if auth_error is not None:
        return auth_error

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
        headers={"X-Run-Id": run_id},
    )


def serve() -> None:
    """Start the RMS FastAPI server using uvicorn."""
    import uvicorn

    cfg = _get_cfg()
    host = cfg.rms_host if cfg is not None else "0.0.0.0"
    port_raw = os.getenv("RMS_PORT") or os.getenv("PORT")
    try:
        port = int(port_raw) if port_raw else (cfg.rms_port if cfg is not None else 8000)
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
    )
