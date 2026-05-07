"""
Typer CLI for the Repo Management Suite.

Usage:
  rms dry-run seo-aeo-geo
  rms dry-run mobile-ux
  rms dry-run on-brand
  rms run seo-aeo-geo          # respects env RMS_DRY_RUN
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Optional

import typer

from repo_mgmt.config import PipelineId, load_settings, ConfigurationError
from repo_mgmt.r2_client import R2Client
from repo_mgmt import pipeline as pipeline_mod

app = typer.Typer(name="rms", help="Repo Management Suite CLI")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

_PIPELINES = ["seo-aeo-geo", "mobile-ux", "on-brand"]


def _validate_pipeline(value: str) -> str:
    if value not in _PIPELINES:
        raise typer.BadParameter(
            f"Unknown pipeline {value!r}. Valid options: {', '.join(_PIPELINES)}"
        )
    return value


@app.command("dry-run")
def dry_run(
    pipeline_id: str = typer.Argument(..., callback=_validate_pipeline, help="Pipeline to run in dry-run mode"),
) -> None:
    """
    Run a pipeline in dry-run mode (no writes, no commits, no pushes).
    """
    typer.echo(f"[rms] dry-run: {pipeline_id}")
    try:
        cfg = load_settings()
    except ConfigurationError as exc:
        typer.secho(f"Configuration error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    r2 = R2Client(cfg)
    report = pipeline_mod.run(pipeline_id, cfg, r2, dry_run=True)  # type: ignore[arg-type]
    _print_report(report)
    if report.error:
        raise typer.Exit(code=1)


@app.command("run")
def run_pipeline(
    pipeline_id: str = typer.Argument(..., callback=_validate_pipeline, help="Pipeline to run"),
    dry_run: Optional[bool] = typer.Option(
        None,
        "--dry-run/--no-dry-run",
        help="Override RMS_DRY_RUN env var",
    ),
) -> None:
    """
    Run a pipeline, respecting RMS_DRY_RUN (or the --dry-run flag).
    """
    typer.echo(f"[rms] run: {pipeline_id}")
    try:
        cfg = load_settings()
    except ConfigurationError as exc:
        typer.secho(f"Configuration error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    r2 = R2Client(cfg)
    report = pipeline_mod.run(pipeline_id, cfg, r2, dry_run=dry_run)  # type: ignore[arg-type]
    _print_report(report)
    if report.error:
        raise typer.Exit(code=1)


def _print_report(report: "pipeline_mod.report_writer.RunReport") -> None:
    """Pretty-print a RunReport summary to stdout."""
    typer.echo("─" * 60)
    typer.echo(f"  run_id   : {report.run_id}")
    typer.echo(f"  pipeline : {report.pipeline}")
    typer.echo(f"  dry_run  : {report.dry_run}")
    typer.echo(f"  applied  : {report.issues_applied}")
    typer.echo(f"  reverted : {report.issues_reverted}")
    typer.echo(f"  skipped  : {report.issues_skipped}")
    typer.echo(f"  guidance : {report.issues_future_guidance}")
    typer.echo(f"  manual   : {report.issues_manual_review}")
    if report.error:
        typer.secho(f"  error    : {report.error}", fg=typer.colors.RED)
    typer.echo("─" * 60)
