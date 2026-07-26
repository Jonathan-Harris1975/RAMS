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
            "RMS_WEBSITE_REPO_PATH": "/tmp/b",
            "RMS_AIMS_REPO_PATH": "/tmp/a",
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

    def test_validation_commands_for_seo_uses_website_validation(self, settings: Settings) -> None:
        cmds = settings.validation_commands_for("seo-aeo-geo")
        assert isinstance(cmds, list)
        assert any("inject_partials" in c for c in cmds)

    def test_validation_commands_for_website(self, settings: Settings) -> None:
        cmds = settings.validation_commands_for("mobile-ux")
        assert isinstance(cmds, list)
        assert any("inject_partials" in c for c in cmds)

    def test_repo_path_for_website_pipelines(self, settings: Settings) -> None:
        from pathlib import Path

        for pid in ("seo-aeo-geo", "mobile-ux"):
            p = settings.repo_path_for(pid)  # type: ignore[arg-type]
            assert isinstance(p, Path)
            assert str(p) == "/tmp/fake-website-repo"

    def test_repo_path_for_on_brand_uses_aims(self, settings: Settings) -> None:
        from pathlib import Path

        p = settings.repo_path_for("on-brand")
        assert isinstance(p, Path)
        assert str(p) == "/tmp/fake-aims-repo"


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
        "RMS_WEBSITE_REPO_PATH": "/tmp/b",
        "RMS_AIMS_REPO_PATH": "/tmp/a",
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


def test_live_write_gate_diagnostics_exposes_values_without_guesswork() -> None:
    with patch.dict(
        os.environ,
        _complete_env(RMS_DRY_RUN="false", RMS_LIVE_WRITE_ENABLED="false"),
        clear=True,
    ):
        cfg = Settings()
    details = cfg.live_write_gate_diagnostics(
        requested_dry_run=False,
        effective_dry_run=False,
    )
    assert details["requestedDryRun"] is False
    assert details["effectiveDryRun"] is False
    assert details["envDryRunRaw"] == "false"
    assert details["envDryRunValue"] is False
    assert details["liveWriteRaw"] == "false"
    assert details["liveWriteValue"] is False
    assert details["liveWritePermitted"] is False
    assert "liveWriteEnvValueIsTrue" in details["failedChecks"]


def test_unresolved_required_secret_reference_fails_closed() -> None:
    env = _complete_env(OPENROUTER_API_KEY="{{ secret.OPENROUTER_API_KEY }}")
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc_info:
            Settings()
    message = str(exc_info.value)
    assert "OPENROUTER_API_KEY" in message
    assert "unresolved secret reference" in message


def test_github_token_placeholder_is_not_treated_as_usable() -> None:
    env = _complete_env(
        RMS_GITHUB_TOKEN="{{ secret.GITHUB_TOKEN_WEBSITE_AUDITS }}",
        GITHUB_TOKEN="{{ secret.GITHUB_TOKEN }}",
    )
    with patch.dict(os.environ, env, clear=True):
        cfg = Settings()
    assert cfg.github_token_value is None


def test_auto_pr_requires_push_enabled() -> None:
    env = _complete_env(
        RMS_CREATE_PR="true",
        RMS_PUSH_ENABLED="false",
        RMS_GITHUB_TOKEN="token",
        RMS_WEBSITE_REPO_URL="https://github.com/example/site.git",
        RMS_AIMS_REPO_URL="https://github.com/example/aims.git",
    )
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError, match="RMS_PUSH_ENABLED=true"):
            Settings()


def test_push_and_auto_pr_require_github_write_configuration() -> None:
    env = _complete_env(RMS_PUSH_ENABLED="true", RMS_CREATE_PR="true")
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc_info:
            Settings()
    message = str(exc_info.value)
    assert "RMS_GITHUB_TOKEN/GITHUB_TOKEN" in message
    assert "RMS_WEBSITE_REPO_URL" in message
    assert "RMS_AIMS_REPO_URL" in message


def test_push_and_auto_pr_production_configuration_loads() -> None:
    env = _complete_env(
        RMS_DRY_RUN="false",
        RMS_LIVE_WRITE_ENABLED="true",
        RMS_PUSH_ENABLED="true",
        RMS_CREATE_PR="true",
        RMS_GITHUB_TOKEN="token",
        RMS_WEBSITE_REPO_URL="https://github.com/example/site.git",
        RMS_AIMS_REPO_URL="https://github.com/example/aims.git",
    )
    with patch.dict(os.environ, env, clear=True):
        cfg = Settings()
    assert cfg.live_write_permitted is True
    assert cfg.rms_push_enabled is True
    assert cfg.rms_create_pr is True
    assert cfg.repo_url_for("website").endswith("site.git")
    assert cfg.repo_branch_for("website") == "main"


def test_website_issue_limit_defaults_to_all_eligible() -> None:
    with patch.dict(os.environ, _complete_env(), clear=True):
        cfg = Settings()
    assert cfg.rms_website_max_issues_per_run == 0
