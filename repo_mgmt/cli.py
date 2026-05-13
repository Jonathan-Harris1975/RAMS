"""
Typer CLI for the Repo Management Suite.

Usage:
  rms dry-run <pipeline>   — run in dry-run mode (no writes, no commits)
  rms run <pipeline>       — run live (respects RMS_DRY_RUN or --dry-run flag)

Console script:
  rms = repo_mgmt.cli:app  (defined in pyproject.toml)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING, Optional, cast

import typer

from repo_mgmt.config import ConfigurationError, PipelineId, load_settings

if TYPE_CHECKING:
    from repo_mgmt.pipeline import RmsPipeline

app = typer.Typer(name="rms", help="Repo Management Suite CLI")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

_PIPELINES: tuple[PipelineId, ...] = ("seo-aeo-geo", "mobile-ux", "on-brand")


def _validate_pipeline(value: str) -> str:
    if value not in _PIPELINES:
        raise typer.BadParameter(
            f"Unknown pipeline {value!r}. Valid: {', '.join(_PIPELINES)}"
        )
    return value


def _build_pipeline(pipeline_id: str) -> "RmsPipeline":
    """Initialise all dependencies and return a ready RmsPipeline."""
    from repo_mgmt.r2_client import R2Client
    from repo_mgmt.model_router import ModelRouter
    from repo_mgmt.pipeline import RmsPipeline

    cfg = load_settings()
    r2 = R2Client(cfg)
    router = ModelRouter(cfg)
    return RmsPipeline.for_id(cast(PipelineId, pipeline_id), cfg, r2, router)


@app.command("dry-run")
def dry_run(
    pipeline_id: str = typer.Argument(
        ..., callback=_validate_pipeline, help="Pipeline to run in dry-run mode"
    ),
) -> None:
    """Run a pipeline in dry-run mode (no writes, no commits, no pushes)."""
    typer.echo(f"[rms] dry-run: {pipeline_id}")
    try:
        pipeline = _build_pipeline(pipeline_id)
    except ConfigurationError as exc:
        typer.secho(f"Configuration error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    report = asyncio.run(pipeline.run(dry_run=True))
    _print_report(report)


@app.command("run")
def run_pipeline(
    pipeline_id: str = typer.Argument(
        ..., callback=_validate_pipeline, help="Pipeline to run"
    ),
    force_dry_run: Optional[bool] = typer.Option(
        None,
        "--dry-run/--no-dry-run",
        help="Override RMS_DRY_RUN env var",
    ),
) -> None:
    """Run a pipeline, respecting RMS_DRY_RUN (or the --dry-run flag)."""
    typer.echo(f"[rms] run: {pipeline_id}")
    try:
        pipeline = _build_pipeline(pipeline_id)
    except ConfigurationError as exc:
        typer.secho(f"Configuration error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    # --dry-run flag overrides env; otherwise use cfg default
    effective_dry_run = (
        force_dry_run if force_dry_run is not None else pipeline.cfg.rms_dry_run
    )
    report = asyncio.run(pipeline.run(dry_run=effective_dry_run))
    _print_report(report)


def _print_report(report: object) -> None:
    """Pretty-print a RunReport summary to stdout."""
    typer.echo("─" * 60)
    typer.echo(f"  runId    : {getattr(report, 'runId', '?')}")
    typer.echo(f"  pipeline : {getattr(report, 'pipeline', '?')}")
    typer.echo(f"  dryRun   : {getattr(report, 'dryRun', '?')}")
    summary = getattr(report, "summary", {})
    typer.echo(f"  committed: {summary.get('committed', 0)}")
    typer.echo(f"  guidance : {summary.get('futureGuidance', 0)}")
    typer.echo(f"  manual   : {summary.get('manualReview', 0)}")
    typer.echo("─" * 60)
