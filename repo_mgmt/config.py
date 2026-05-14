"""
Configuration module for the Repo Management Suite.

Loads all settings from environment variables (or a .env file) via
pydantic-settings. Required operational values are validated with a structured
ConfigurationError rather than raw KeyError-style failures. Boolean safety gates
are deliberately fail-closed: absent, empty, or malformed values resolve to the
safe value and keep metadata showing that live mode was not explicitly enabled.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PipelineId = Literal["seo-aeo-geo", "mobile-ux", "on-brand"]


class ConfigurationError(Exception):
    """Raised when one or more required configuration fields are missing or invalid."""


_TRUE_VALUES = frozenset({"true", "1", "yes", "y", "on"})
_FALSE_VALUES = frozenset({"false", "0", "no", "n", "off"})


def _env_get_case_insensitive(name: str) -> str | None:
    """Return an environment value using case-insensitive lookup."""
    if name in os.environ:
        return os.environ[name]
    lowered = name.lower()
    for key, value in os.environ.items():
        if key.lower() == lowered:
            return value
    return None


def _parse_bool(value: object, *, safe_default: bool) -> tuple[bool, bool]:
    """Parse a boolean-ish value and report whether parsing was explicit."""
    if isinstance(value, bool):
        return value, True
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in _TRUE_VALUES:
            return True, True
        if stripped in _FALSE_VALUES:
            return False, True
    return safe_default, False


class Settings(BaseSettings):
    """All RMS runtime settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Cloudflare R2 ──────────────────────────────────────────────────────
    r2_endpoint: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_region: str = "auto"
    r2_bucket_audits: str = "audits"
    r2_public_base_url_audits: str = ""

    # ── OpenRouter ─────────────────────────────────────────────────────────
    openrouter_api_base: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = ""
    openrouter_primary_model: str = ""
    openrouter_secondary_model: str = ""
    openrouter_triage_model: str = ""

    # ── Target repo paths ──────────────────────────────────────────────────
    rms_seo_repo_path: str = ""
    rms_website_repo_path: str = ""

    # ── Per-target validation commands (split on " && ") ──────────────────
    rms_seo_validation_commands: str = "npm test && npm run build"
    rms_website_validation_commands: str = (
        "python3 scripts/inject_partials.py --validate"
        " && python3 scripts/sync_redirects.py --check"
        " && python3 scripts/check_crawlers.py"
    )

    # ── Behaviour defaults ─────────────────────────────────────────────────
    rms_dry_run: bool = True  # SAFE DEFAULT — never omit
    rms_live_write_enabled: bool = False
    rms_max_issues_per_run: int = 5
    rms_report_prefix: str = "qa-suite/reports"
    rms_report_dir: str = "/tmp/rams-reports"
    rms_qa_branch_prefix: str = "rms-qa/"
    rms_push_enabled: bool = False
    rms_create_pr: bool = False
    rms_validate_after_each_task: bool = True
    rms_revert_on_validation_failure: bool = True

    # ── Deployment guard ───────────────────────────────────────────────────
    rms_single_worker_mode: bool = True

    # ── API server ─────────────────────────────────────────────────────────
    rms_host: str = "0.0.0.0"
    rms_port: int = 8000
    log_level: str = "info"

    _rms_dry_run_env_present: bool = PrivateAttr(default=False)
    _rms_dry_run_env_parseable: bool = PrivateAttr(default=False)
    _rms_live_write_env_present: bool = PrivateAttr(default=False)
    _rms_live_write_env_parseable: bool = PrivateAttr(default=False)

    @field_validator(
        "rms_dry_run",
        "rms_live_write_enabled",
        "rms_push_enabled",
        "rms_create_pr",
        "rms_validate_after_each_task",
        "rms_revert_on_validation_failure",
        "rms_single_worker_mode",
        mode="before",
    )
    @classmethod
    def _validate_bool_fields(cls, value: object, info: object) -> bool:
        """Parse boolean environment values with fail-closed defaults."""
        field_name = getattr(info, "field_name", "")
        safe_default = True if field_name in {
            "rms_dry_run",
            "rms_validate_after_each_task",
            "rms_revert_on_validation_failure",
            "rms_single_worker_mode",
        } else False
        parsed, _ = _parse_bool(value, safe_default=safe_default)
        return parsed

    @model_validator(mode="after")
    def _check_required(self) -> "Settings":
        """Raise ConfigurationError listing all missing required fields."""
        dry_raw = _env_get_case_insensitive("RMS_DRY_RUN")
        live_raw = _env_get_case_insensitive("RMS_LIVE_WRITE_ENABLED")
        self._rms_dry_run_env_present = dry_raw is not None
        self._rms_dry_run_env_parseable = _parse_bool(
            dry_raw, safe_default=True
        )[1] if dry_raw is not None else False
        self._rms_live_write_env_present = live_raw is not None
        self._rms_live_write_env_parseable = _parse_bool(
            live_raw, safe_default=False
        )[1] if live_raw is not None else False

        missing: list[str] = []
        required = {
            "R2_ENDPOINT": self.r2_endpoint,
            "R2_ACCESS_KEY_ID": self.r2_access_key_id,
            "R2_SECRET_ACCESS_KEY": self.r2_secret_access_key,
            "OPENROUTER_API_KEY": self.openrouter_api_key,
            "OPENROUTER_PRIMARY_MODEL": self.openrouter_primary_model,
            "OPENROUTER_SECONDARY_MODEL": self.openrouter_secondary_model,
            "OPENROUTER_TRIAGE_MODEL": self.openrouter_triage_model,
            "RMS_SEO_REPO_PATH": self.rms_seo_repo_path,
            "RMS_WEBSITE_REPO_PATH": self.rms_website_repo_path,
        }
        for name, value in required.items():
            if not str(value).strip():
                missing.append(name)
        if missing:
            raise ConfigurationError(
                f"Missing required configuration fields: {', '.join(missing)}"
            )
        return self

    @property
    def dry_run_env_explicit_and_parseable(self) -> bool:
        """Return True only when RMS_DRY_RUN was present and parseable."""
        return self._rms_dry_run_env_present and self._rms_dry_run_env_parseable

    @property
    def live_write_env_explicit_and_parseable(self) -> bool:
        """Return True only when RMS_LIVE_WRITE_ENABLED was present and parseable."""
        return self._rms_live_write_env_present and self._rms_live_write_env_parseable

    @property
    def live_write_permitted(self) -> bool:
        """Return True only when both independent live-write gates are open."""
        return (
            self.dry_run_env_explicit_and_parseable
            and self.rms_dry_run is False
            and self.live_write_env_explicit_and_parseable
            and self.rms_live_write_enabled is True
        )

    def validation_commands_for(self, pipeline: PipelineId) -> list[str]:
        """Return the ordered validation commands for the given pipeline."""
        if pipeline == "seo-aeo-geo":
            raw = self.rms_seo_validation_commands
        else:
            raw = self.rms_website_validation_commands
        return [cmd.strip() for cmd in raw.split("&&") if cmd.strip()]

    def repo_path_for(self, pipeline: PipelineId) -> Path:
        """Return the absolute repo path for the given pipeline."""
        if pipeline == "seo-aeo-geo":
            return Path(self.rms_seo_repo_path)
        return Path(self.rms_website_repo_path)

    def report_dir(self) -> Path:
        """Return the configured local report directory."""
        return Path(self.rms_report_dir)


def configured_worker_count() -> int:
    """Return the configured worker count from common runtime environment names."""
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        raw = _env_get_case_insensitive(name)
        if raw is None:
            continue
        try:
            parsed = int(raw)
        except ValueError:
            return 2
        return max(parsed, 1)
    return 1


def load_settings() -> Settings:
    """Load and validate settings, raising ConfigurationError on failure."""
    try:
        return Settings()
    except ConfigurationError:
        raise
    except Exception as exc:
        raise ConfigurationError(f"Failed to load configuration: {exc}") from exc
