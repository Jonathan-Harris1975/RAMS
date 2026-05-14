"""Tests for repo_mgmt.config."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from repo_mgmt.config import ConfigurationError, Settings, load_settings


class TestSettingsValidation:
    def test_valid_settings_loads(self, settings: Settings) -> None:
        assert settings.r2_endpoint.startswith("https://")
        assert settings.openrouter_api_key == "test-or-key"

    def test_dry_run_defaults_true_if_missing(self) -> None:
        env = {
            "R2_ENDPOINT": "https://x.r2.example.com",
            "R2_ACCESS_KEY_ID": "k",
            "R2_SECRET_ACCESS_KEY": "s",
            "OPENROUTER_API_KEY": "or",
            "OPENROUTER_PRIMARY_MODEL": "m1",
            "OPENROUTER_SECONDARY_MODEL": "m2",
            "OPENROUTER_TRIAGE_MODEL": "m3",
            "RMS_SEO_REPO_PATH": "/tmp/a",
            "RMS_WEBSITE_REPO_PATH": "/tmp/b",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = Settings()
        # No RMS_DRY_RUN set — should default to True (safe)
        assert cfg.rms_dry_run is True

    def test_dry_run_parseable_false(self) -> None:
        from tests.conftest import VALID_ENV

        env = {**VALID_ENV, "RMS_DRY_RUN": "false"}
        with patch.dict(os.environ, env, clear=False):
            cfg = Settings()
        assert cfg.rms_dry_run is False

    def test_missing_required_fields_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ConfigurationError) as exc_info:
                Settings()
        assert "R2_ENDPOINT" in str(exc_info.value) or "Missing" in str(exc_info.value)

    def test_validation_commands_for_seo(self, settings: Settings) -> None:
        cmds = settings.validation_commands_for("seo-aeo-geo")
        assert isinstance(cmds, list)
        assert len(cmds) >= 1
        assert all(isinstance(c, str) for c in cmds)

    def test_validation_commands_for_website(self, settings: Settings) -> None:
        cmds = settings.validation_commands_for("mobile-ux")
        assert isinstance(cmds, list)
        assert any("inject_partials" in c for c in cmds)

    def test_repo_path_for_seo(self, settings: Settings) -> None:
        from pathlib import Path

        p = settings.repo_path_for("seo-aeo-geo")
        assert isinstance(p, Path)
        assert str(p) == "/tmp/fake-seo-repo"

    def test_repo_path_for_website_pipelines(self, settings: Settings) -> None:
        from pathlib import Path

        for pid in ("mobile-ux", "on-brand"):
            p = settings.repo_path_for(pid)  # type: ignore[arg-type]
            assert isinstance(p, Path)
            assert str(p) == "/tmp/fake-website-repo"


class TestLoadSettings:
    def test_load_settings_raises_configuration_error_on_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ConfigurationError):
                load_settings()


def _complete_env(**overrides: str) -> dict[str, str]:
    env = {
        "R2_ENDPOINT": "https://x.r2.example.com",
        "R2_ACCESS_KEY_ID": "k",
        "R2_SECRET_ACCESS_KEY": "s",
        "OPENROUTER_API_KEY": "or",
        "OPENROUTER_PRIMARY_MODEL": "m1",
        "OPENROUTER_SECONDARY_MODEL": "m2",
        "OPENROUTER_TRIAGE_MODEL": "m3",
        "RMS_SEO_REPO_PATH": "/tmp/a",
        "RMS_WEBSITE_REPO_PATH": "/tmp/b",
    }
    env.update(overrides)
    return env


def test_absent_rms_dry_run_is_safe_default_not_live_permitted() -> None:
    with patch.dict(os.environ, _complete_env(), clear=True):
        cfg = Settings()
    assert cfg.rms_dry_run is True
    assert cfg.dry_run_env_explicit_and_parseable is False
    assert cfg.live_write_permitted is False


def test_malformed_rms_dry_run_is_safe_default_not_live_permitted() -> None:
    with patch.dict(os.environ, _complete_env(RMS_DRY_RUN="absolutely-not"), clear=True):
        cfg = Settings()
    assert cfg.rms_dry_run is True
    assert cfg.dry_run_env_explicit_and_parseable is False
    assert cfg.live_write_permitted is False


def test_live_mode_requires_both_parseable_env_gates() -> None:
    with patch.dict(
        os.environ,
        _complete_env(RMS_DRY_RUN="false", RMS_LIVE_WRITE_ENABLED="true"),
        clear=True,
    ):
        cfg = Settings()
    assert cfg.rms_dry_run is False
    assert cfg.rms_live_write_enabled is True
    assert cfg.live_write_permitted is True


def test_live_mode_not_permitted_when_live_gate_malformed() -> None:
    with patch.dict(
        os.environ,
        _complete_env(RMS_DRY_RUN="false", RMS_LIVE_WRITE_ENABLED="wat"),
        clear=True,
    ):
        cfg = Settings()
    assert cfg.rms_dry_run is False
    assert cfg.rms_live_write_enabled is False
    assert cfg.live_write_permitted is False
