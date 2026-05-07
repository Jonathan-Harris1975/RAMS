"""Tests for repo_mgmt.validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_mgmt.validator import ValidationResult, run


class TestValidatorDryRun:
    def test_dry_run_always_passes(self, settings, tmp_path: Path) -> None:
        result = run("on-brand", tmp_path, settings, dry_run=True)
        assert result.passed is True
        assert "[dry-run" in result.outputs[0]

    def test_dry_run_returns_all_commands(self, settings, tmp_path: Path) -> None:
        result = run("mobile-ux", tmp_path, settings, dry_run=True)
        assert len(result.commands) == len(settings.validation_commands_for("mobile-ux"))


class TestValidatorLive:
    def test_passing_command(self, settings, tmp_path: Path) -> None:
        settings.rms_seo_validation_commands = "echo ok"
        result = run("seo-aeo-geo", tmp_path, settings, dry_run=False)
        assert result.passed is True
        assert result.failed_command is None

    def test_failing_command(self, settings, tmp_path: Path) -> None:
        settings.rms_seo_validation_commands = "exit 1"
        result = run("seo-aeo-geo", tmp_path, settings, dry_run=False)
        assert result.passed is False
        assert result.return_code == 1

    def test_stops_at_first_failure(self, settings, tmp_path: Path) -> None:
        settings.rms_seo_validation_commands = "exit 1 && echo second"
        result = run("seo-aeo-geo", tmp_path, settings, dry_run=False)
        assert result.passed is False
        # Only one output recorded because we stop at first failure
        assert len(result.outputs) == 1
