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
import re
from pathlib import Path
from typing import Literal

from pydantic import Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PipelineId = Literal["website", "seo-aeo-geo", "mobile-ux", "on-brand"]


class ConfigurationError(Exception):
    """Raised when one or more required configuration fields are missing or invalid."""


_TRUE_VALUES = frozenset({"true", "1", "yes", "y", "on"})
_FALSE_VALUES = frozenset({"false", "0", "no", "n", "off"})



def _is_unresolved_secret_reference(value: object) -> bool:
    """Return True when a runtime value is still a Koyeb secret placeholder."""
    if not isinstance(value, str):
        return False
    return re.fullmatch(
        r"\{\{\s*secret\.[^}]+\}\}", value.strip(), flags=re.IGNORECASE
    ) is not None


def _configured_value(value: object) -> str:
    """Return a stripped config value, hiding unresolved secret placeholders."""
    if _is_unresolved_secret_reference(value):
        return ""
    return str(value or "").strip()

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
    r2_bucket_hive_skills: str = "hive-skills"
    r2_public_base_url_hive_skills: str = (
        "https://pub-da50a6512f164566955a3076a1c795ef.r2.dev"
    )

    # ── OpenRouter ─────────────────────────────────────────────────────────
    openrouter_api_base: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = ""
    openrouter_primary_model: str = ""
    openrouter_secondary_model: str = ""
    openrouter_triage_model: str = ""
    openrouter_http_referer: str = ""
    openrouter_app_name: str = "RAMS"

    # eMicro-safe OpenRouter transport and generation controls.
    rms_openrouter_connect_timeout_seconds: float = Field(default=5.0, ge=1.0, le=30.0)
    rms_openrouter_read_timeout_seconds: float = Field(default=90.0, ge=10.0, le=300.0)
    rms_openrouter_write_timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)
    rms_openrouter_pool_timeout_seconds: float = Field(default=5.0, ge=1.0, le=30.0)
    rms_openrouter_max_connections: int = Field(default=2, ge=1, le=4)
    rms_openrouter_max_keepalive_connections: int = Field(default=1, ge=0, le=2)
    rms_openrouter_keepalive_expiry_seconds: float = Field(
        default=30.0, ge=5.0, le=120.0
    )
    rms_openrouter_max_retries: int = Field(default=0, ge=0, le=2)
    rms_openrouter_retry_base_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    rms_openrouter_retry_max_seconds: float = Field(default=8.0, ge=1.0, le=60.0)
    rms_openrouter_provider_sort: Literal["price", "throughput", "latency"] = "price"
    rms_openrouter_allow_fallbacks: bool = True
    rms_openrouter_data_collection: Literal["allow", "deny"] = "deny"
    rms_primary_max_tokens: int = Field(default=3072, ge=512, le=8192)
    rms_secondary_max_tokens: int = Field(default=3072, ge=512, le=8192)
    rms_triage_max_tokens: int = Field(default=128, ge=32, le=512)
    rms_primary_temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    rms_triage_temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    rms_top_p: float = Field(default=0.9, ge=0.1, le=1.0)
    rms_openrouter_log_usage: bool = True
    rms_openrouter_log_cost: bool = True
    rms_openrouter_log_prompts: bool = False

    # ── Target repo paths ──────────────────────────────────────────────────
    # Current architecture:
    #   website     -> website repo (unified Digital Growth + SEO/AEO/GEO + Mobile UX report)
    #   seo-aeo-geo -> website repo (legacy compatibility)
    #   mobile-ux   -> website repo (legacy compatibility)
    #   on-brand    -> AIMS / AI-management-suite repo
    rms_website_repo_path: str = ""
    rms_aims_repo_path: str = ""
    rms_seo_repo_path: str = ""  # legacy alias; no longer used for SEO routing

    # ── Optimisation Subsystem (deterministic self-adjusting QA for AIMS) ──
    # Thresholds, tiers, and category ceilings are NEVER hard-coded here;
    # they live in the externally configured policy file loaded by
    # repo_mgmt.optimisation.policy.load_policy(). This flag is a global,
    # fail-closed kill switch: even if a category is enabled in the policy
    # file, the optimisation engine's auto_configure/patch_candidate
    # routing does nothing unless this is explicitly true.
    rms_optimisation_enabled: bool = False
    rms_optimisation_policy_path: str = ""
    rms_optimisation_state_dir: str = "data/optimisation_history"
    rms_optimisation_rollback_dir: str = "data/optimisation_rollback"

    # ── Optional Koyeb/runtime repo bootstrap ──────────────────────────────
    rms_repo_bootstrap_enabled: bool = False
    rms_repo_base_dir: str = "/tmp/rams-repos"
    rms_website_repo_url: str = ""
    rms_website_repo_branch: str = "main"
    rms_aims_repo_url: str = ""
    rms_aims_repo_branch: str = "main"
    github_token: str | None = None
    rms_github_token: str | None = None
    rms_github_api_base: str = "https://api.github.com"
    rms_github_api_timeout_seconds: float = Field(default=20.0, ge=3.0, le=60.0)
    rms_github_api_max_retries: int = Field(default=2, ge=0, le=4)

    # ── Per-target validation commands (split on " && ") ──────────────────
    rms_aims_validation_commands: str = "npm test && npm run build"
    rms_seo_validation_commands: str = "npm test && npm run build"  # legacy alias
    rms_website_validation_commands: str = (
        "python3 scripts/inject_partials.py --validate"
        " && python3 scripts/sync_redirects.py --check"
        " && python3 scripts/check_crawlers.py"
    )

    # ── API authentication ────────────────────────────────────────────────
    # Rebuild/write endpoints require RMS_API_KEY unless a local developer
    # explicitly opts out with RMS_ALLOW_UNAUTHENTICATED_DEV=true.
    rms_api_key: str | None = Field(default=None)
    rms_allow_unauthenticated_dev: bool = False

    # ── Behaviour defaults ─────────────────────────────────────────────────
    rms_dry_run: bool = True  # SAFE DEFAULT — never omit
    rms_live_write_enabled: bool = False
    rms_max_issues_per_run: int = Field(default=1, ge=1, le=5)
    # Unified website pipeline: 0 means process every eligible confirmed code_fix
    # in the final council ledger. Other lanes retain the bounded global cap.
    rms_website_max_issues_per_run: int = Field(default=0, ge=0, le=100)
    rms_max_concurrent_pipelines: int = Field(default=1, ge=1, le=1)

    # eMicro resource ceilings. These bound RAM, disk, subprocess output and
    # evidence sent to model providers without weakening live-write gates.
    rms_max_audit_artefacts: int = Field(default=8, ge=1, le=20)
    rms_max_audit_object_bytes: int = Field(default=1_048_576, ge=65_536, le=8_388_608)
    rms_max_audit_total_bytes: int = Field(default=4_194_304, ge=262_144, le=33_554_432)
    rms_max_context_files: int = Field(default=8, ge=1, le=40)
    rms_max_context_file_bytes: int = Field(default=131_072, ge=8_192, le=1_048_576)
    rms_max_context_total_bytes: int = Field(default=524_288, ge=65_536, le=4_194_304)
    rms_max_indexed_files: int = Field(default=20_000, ge=100, le=100_000)
    rms_validation_timeout_seconds: int = Field(default=240, ge=10, le=900)
    rms_validation_output_max_lines: int = Field(default=120, ge=20, le=1000)
    rms_validation_output_max_bytes: int = Field(
        default=131_072, ge=8_192, le=1_048_576
    )
    rms_git_timeout_seconds: int = Field(default=120, ge=10, le=600)
    rms_git_output_max_bytes: int = Field(default=65_536, ge=8_192, le=1_048_576)
    rms_git_clone_depth: int = Field(default=1, ge=1, le=5)
    rms_temp_cleanup_enabled: bool = True
    rms_temp_max_age_hours: int = Field(default=24, ge=1, le=168)
    rms_min_free_disk_mb: int = Field(default=256, ge=64, le=1024)
    rms_shutdown_grace_seconds: int = Field(default=25, ge=5, le=29)
    rms_readiness_cache_seconds: int = Field(default=300, ge=10, le=1800)
    rms_idempotency_cache_size: int = Field(default=128, ge=16, le=1024)
    rms_busy_retry_after_seconds: int = Field(default=60, ge=5, le=900)
    rms_report_max_bytes: int = Field(default=4_194_304, ge=262_144, le=16_777_216)

    # Professional operations: recurring storage verification, retention metadata,
    # release evidence and central HIVE operational alerts.
    rms_r2_verify_interval_seconds: int = Field(default=900, ge=60, le=86_400)
    rms_report_retention_days: int = Field(default=180, ge=7, le=3650)
    rms_release_id: str = ""
    ops_alert_webhook_url: str = ""
    ops_alert_webhook_token: str = ""
    ops_alert_timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)

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
    _rms_dry_run_env_raw: str | None = PrivateAttr(default=None)
    _rms_live_write_env_present: bool = PrivateAttr(default=False)
    _rms_live_write_env_parseable: bool = PrivateAttr(default=False)
    _rms_live_write_env_raw: str | None = PrivateAttr(default=None)

    @field_validator(
        "rms_dry_run",
        "rms_live_write_enabled",
        "rms_push_enabled",
        "rms_create_pr",
        "rms_validate_after_each_task",
        "rms_revert_on_validation_failure",
        "rms_single_worker_mode",
        "rms_repo_bootstrap_enabled",
        "rms_optimisation_enabled",
        "rms_allow_unauthenticated_dev",
        "rms_openrouter_allow_fallbacks",
        "rms_openrouter_log_usage",
        "rms_openrouter_log_cost",
        "rms_openrouter_log_prompts",
        "rms_temp_cleanup_enabled",
        mode="before",
    )
    @classmethod
    def _validate_bool_fields(cls, value: object, info: object) -> bool:
        """Parse boolean environment values with fail-closed defaults."""
        field_name = getattr(info, "field_name", "")
        safe_default = (
            True
            if field_name
            in {
                "rms_dry_run",
                "rms_validate_after_each_task",
                "rms_revert_on_validation_failure",
                "rms_single_worker_mode",
                "rms_openrouter_allow_fallbacks",
                "rms_openrouter_log_usage",
                "rms_openrouter_log_cost",
                "rms_temp_cleanup_enabled",
            }
            else False
        )
        parsed, _ = _parse_bool(value, safe_default=safe_default)
        return parsed

    @model_validator(mode="after")
    def _check_required(self) -> "Settings":
        """Raise ConfigurationError listing all missing required fields."""
        dry_raw = _env_get_case_insensitive("RMS_DRY_RUN")
        live_raw = _env_get_case_insensitive("RMS_LIVE_WRITE_ENABLED")
        self._rms_dry_run_env_raw = dry_raw
        self._rms_live_write_env_raw = live_raw
        self._rms_dry_run_env_present = dry_raw is not None
        self._rms_dry_run_env_parseable = (
            _parse_bool(dry_raw, safe_default=True)[1] if dry_raw is not None else False
        )
        self._rms_live_write_env_present = live_raw is not None
        self._rms_live_write_env_parseable = (
            _parse_bool(live_raw, safe_default=False)[1]
            if live_raw is not None
            else False
        )

        missing: list[str] = []
        required = {
            "R2_ENDPOINT": self.r2_endpoint,
            "R2_ACCESS_KEY_ID": self.r2_access_key_id,
            "R2_SECRET_ACCESS_KEY": self.r2_secret_access_key,
            "OPENROUTER_API_KEY": self.openrouter_api_key,
            "OPENROUTER_PRIMARY_MODEL": self.openrouter_primary_model,
            "OPENROUTER_SECONDARY_MODEL": self.openrouter_secondary_model,
            "OPENROUTER_TRIAGE_MODEL": self.openrouter_triage_model,
            "RMS_WEBSITE_REPO_PATH": self.rms_website_repo_path,
            "RMS_AIMS_REPO_PATH": self.aims_repo_path_value,
        }
        if self.rms_repo_bootstrap_enabled:
            required.update(
                {
                    "RMS_WEBSITE_REPO_URL": self.rms_website_repo_url,
                    "RMS_AIMS_REPO_URL": self.rms_aims_repo_url,
                }
            )
        for name, value in required.items():
            if _is_unresolved_secret_reference(value):
                missing.append(f"{name} (unresolved secret reference)")
            elif not _configured_value(value):
                missing.append(name)
        if self.rms_create_pr and not self.rms_push_enabled:
            missing.append("RMS_PUSH_ENABLED=true (required when RMS_CREATE_PR=true)")
        if self.rms_push_enabled or self.rms_create_pr:
            if not _configured_value(self.github_token_value):
                missing.append("RMS_GITHUB_TOKEN/GITHUB_TOKEN (GitHub write token)")
            for name, value in (
                ("RMS_WEBSITE_REPO_URL", self.rms_website_repo_url),
                ("RMS_AIMS_REPO_URL", self.rms_aims_repo_url),
            ):
                if _is_unresolved_secret_reference(value):
                    if f"{name} (unresolved secret reference)" not in missing:
                        missing.append(f"{name} (unresolved secret reference)")
                elif not _configured_value(value) and name not in missing:
                    missing.append(name)
        if missing:
            raise ConfigurationError(
                f"Missing required configuration fields: {', '.join(missing)}"
            )
        return self

    @property
    def dry_run_env_raw(self) -> str | None:
        """Return the raw RMS_DRY_RUN environment value, if present."""
        return self._rms_dry_run_env_raw

    @property
    def dry_run_env_present(self) -> bool:
        """Return True when RMS_DRY_RUN was present in the runtime environment."""
        return self._rms_dry_run_env_present

    @property
    def dry_run_env_parseable(self) -> bool:
        """Return True when RMS_DRY_RUN was parseable as a boolean value."""
        return self._rms_dry_run_env_parseable

    @property
    def live_write_env_raw(self) -> str | None:
        """Return the raw RMS_LIVE_WRITE_ENABLED environment value, if present."""
        return self._rms_live_write_env_raw

    @property
    def live_write_env_present(self) -> bool:
        """Return True when RMS_LIVE_WRITE_ENABLED was present in the runtime environment."""
        return self._rms_live_write_env_present

    @property
    def live_write_env_parseable(self) -> bool:
        """Return True when RMS_LIVE_WRITE_ENABLED was parseable as a boolean value."""
        return self._rms_live_write_env_parseable

    def live_write_gate_diagnostics(
        self, *, requested_dry_run: bool | None, effective_dry_run: bool
    ) -> dict[str, object]:
        """Return non-secret live-write gate diagnostics for API error responses."""
        checks = {
            "requestDryRunIsFalse": effective_dry_run is False,
            "dryRunEnvPresent": self.dry_run_env_present,
            "dryRunEnvParseable": self.dry_run_env_parseable,
            "dryRunEnvValueIsFalse": self.rms_dry_run is False,
            "liveWriteEnvPresent": self.live_write_env_present,
            "liveWriteEnvParseable": self.live_write_env_parseable,
            "liveWriteEnvValueIsTrue": self.rms_live_write_enabled is True,
        }
        return {
            "requestedDryRun": requested_dry_run,
            "effectiveDryRun": effective_dry_run,
            "envDryRunRaw": self.dry_run_env_raw,
            "envDryRunValue": self.rms_dry_run,
            "envDryRunPresent": self.dry_run_env_present,
            "envDryRunParseable": self.dry_run_env_parseable,
            "liveWriteRaw": self.live_write_env_raw,
            "liveWriteValue": self.rms_live_write_enabled,
            "liveWritePresent": self.live_write_env_present,
            "liveWriteParseable": self.live_write_env_parseable,
            "liveWritePermitted": self.live_write_permitted,
            "checks": checks,
            "failedChecks": [name for name, passed in checks.items() if not passed],
        }

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

    @property
    def aims_repo_path_value(self) -> str:
        """Return the AIMS repo path, using the legacy SEO path only as a fallback."""
        return self.rms_aims_repo_path or self.rms_seo_repo_path

    @property
    def github_token_value(self) -> str | None:
        """Return the runtime GitHub token from supported env aliases.

        Koyeb deployments normally use RMS_GITHUB_TOKEN for application-owned
        secrets, while local/GitHub-hosted environments often expose
        GITHUB_TOKEN. Supporting both keeps the bootstrap contract explicit
        without breaking existing deployments.
        """
        for candidate in (self.rms_github_token, self.github_token):
            if _configured_value(candidate):
                return _configured_value(candidate)
        return None


    def repo_url_for(self, pipeline: PipelineId) -> str:
        """Return the configured GitHub repository URL for a pipeline."""
        if pipeline in {"website", "seo-aeo-geo", "mobile-ux"}:
            return self.rms_website_repo_url
        if pipeline == "on-brand":
            return self.rms_aims_repo_url
        raise ConfigurationError(f"Unknown pipeline: {pipeline}")

    def repo_branch_for(self, pipeline: PipelineId) -> str:
        """Return the configured protected base branch for a pipeline."""
        if pipeline in {"website", "seo-aeo-geo", "mobile-ux"}:
            return self.rms_website_repo_branch
        if pipeline == "on-brand":
            return self.rms_aims_repo_branch
        raise ConfigurationError(f"Unknown pipeline: {pipeline}")

    def validation_commands_for(self, pipeline: PipelineId) -> list[str]:
        """Return the ordered validation commands for the given pipeline."""
        if pipeline in {"website", "seo-aeo-geo", "mobile-ux"}:
            raw = self.rms_website_validation_commands
        elif pipeline == "on-brand":
            raw = self.rms_aims_validation_commands or self.rms_seo_validation_commands
        else:
            raise ConfigurationError(f"Unknown pipeline: {pipeline}")
        return [cmd.strip() for cmd in raw.split("&&") if cmd.strip()]

    def repo_path_for(self, pipeline: PipelineId) -> Path:
        """Return the absolute repo path for the given pipeline."""
        if pipeline in {"website", "seo-aeo-geo", "mobile-ux"}:
            return Path(self.rms_website_repo_path)
        if pipeline == "on-brand":
            return Path(self.aims_repo_path_value)
        raise ConfigurationError(f"Unknown pipeline: {pipeline}")

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
