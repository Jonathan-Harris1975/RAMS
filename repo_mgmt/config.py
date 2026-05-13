"""
Configuration module for the Repo Management Suite.

Loads all settings from environment variables (or a .env file) via
pydantic-settings. Validates at import time and raises ConfigurationError
listing every missing required field — never crashes with a raw KeyError.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PipelineId = Literal["seo-aeo-geo", "mobile-ux", "on-brand"]


class ConfigurationError(Exception):
    """Raised when one or more required configuration fields are missing or invalid."""


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
    rms_max_issues_per_run: int = 5
    rms_report_prefix: str = "qa-suite/reports"
    rms_qa_branch_prefix: str = "rms-qa/"
    rms_push_enabled: bool = False
    rms_create_pr: bool = False
    rms_validate_after_each_task: bool = True
    rms_revert_on_validation_failure: bool = True

    # ── API server ─────────────────────────────────────────────────────────
    rms_host: str = "0.0.0.0"
    rms_port: int = 8000
    log_level: str = "info"

    @field_validator("rms_dry_run", mode="before")
    @classmethod
    def _validate_dry_run(cls, v: object) -> bool:
        """
        Ensure RMS_DRY_RUN is always parseable. Service refuses to proceed
        if the value is absent or unparseable — defaults to True (safe).
        """
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            if v.lower() in ("true", "1", "yes"):
                return True
            if v.lower() in ("false", "0", "no"):
                return False
        # Unparseable → safe default True
        return True

    @model_validator(mode="after")
    def _check_required(self) -> "Settings":
        """Raise ConfigurationError listing all missing required fields."""
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
            if not value:
                missing.append(name)
        if missing:
            raise ConfigurationError(
                f"Missing required configuration fields: {', '.join(missing)}"
            )
        return self

    # ── Convenience helpers ────────────────────────────────────────────────

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


def load_settings() -> Settings:
    """Load and validate settings, raising ConfigurationError on failure."""
    try:
        return Settings()
    except ConfigurationError:
        raise
    except Exception as exc:
        raise ConfigurationError(f"Failed to load configuration: {exc}") from exc
