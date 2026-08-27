"""Deployment-contract tests for the Koyeb production environment boundary."""

from __future__ import annotations

import shlex
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_KOYEB_BINDINGS = {
    "OPENROUTER_API_KEY": "{{ secret.OPENROUTER_API_KEY }}",
    "R2_ACCESS_KEY_ID": "{{ secret.R2_ACCESS_KEY_ID }}",
    "R2_SECRET_ACCESS_KEY": "{{ secret.R2_SECRET_ACCESS_KEY }}",
    "RMS_API_KEY": "{{ secret.RMS_API_KEY }}",
    "RMS_GITHUB_TOKEN": "{{ secret.GITHUB_TOKEN_WEBSITE_AUDITS }}",
    "RMS_WEBSITE_REPO_URL": "{{ secret.RMS_WEBSITE_REPO_URL }}",
    "RMS_AIMS_REPO_URL": "{{ secret.RMS_AIMS_REPO_URL }}",
}

EXPECTED_IMAGE_DEFAULTS = {
    "APP_ENV": "production",
    "LOG_LEVEL": "info",
    "MALLOC_ARENA_MAX": "2",
    "NODE_OPTIONS": "--max-old-space-size=256",
    "OPENROUTER_API_BASE": "https://openrouter.ai/api/v1",
    "OPENROUTER_APP_NAME": "RAMS",
    "OPENROUTER_HTTP_REFERER": "https://jonathan-harris.online",
    "OPENROUTER_PRIMARY_MODEL": "anthropic/claude-sonnet-4-6",
    "OPENROUTER_SECONDARY_MODEL": "openai/gpt-4o-mini",
    "OPENROUTER_TRIAGE_MODEL": "google/gemini-2.5-flash-lite",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "random",
    "PYTHONUNBUFFERED": "1",
    "R2_BUCKET_AUDITS": "audits",
    "R2_BUCKET_HIVE_SKILLS": "hive-skills",
    "R2_ENDPOINT": "https://3fb60a7136e950a7ec74959b45e4635e.r2.cloudflarestorage.com",
    "R2_PUBLIC_BASE_URL_AUDITS": "https://pub-f6b6cfd7d07e46f695d08e4a8dc3bd6b.r2.dev",
    "R2_PUBLIC_BASE_URL_HIVE_SKILLS": "https://pub-da50a6512f164566955a3076a1c795ef.r2.dev",
    "R2_REGION": "auto",
    "RMS_AIMS_REPO_BRANCH": "main",
    "RMS_AIMS_REPO_PATH": "/tmp/rams-repos/aims",
    "RMS_AIMS_VALIDATION_COMMANDS": "npm test && npm run build",
    "RMS_ALLOW_UNAUTHENTICATED_DEV": "false",
    "RMS_BUSY_RETRY_AFTER_SECONDS": "60",
    "RMS_CREATE_PR": "false",
    "RMS_DRY_RUN": "false",
    "RMS_GITHUB_API_BASE": "https://api.github.com",
    "RMS_GITHUB_API_MAX_RETRIES": "2",
    "RMS_GITHUB_API_TIMEOUT_SECONDS": "20",
    "RMS_GIT_CLONE_DEPTH": "1",
    "RMS_GIT_OUTPUT_MAX_BYTES": "65536",
    "RMS_GIT_TIMEOUT_SECONDS": "120",
    "RMS_HOST": "0.0.0.0",
    "RMS_IDEMPOTENCY_CACHE_SIZE": "128",
    "RMS_LIVE_WRITE_ENABLED": "true",
    "RMS_MAX_AUDIT_ARTEFACTS": "8",
    "RMS_MAX_AUDIT_OBJECT_BYTES": "1048576",
    "RMS_MAX_AUDIT_TOTAL_BYTES": "4194304",
    "RMS_MAX_CONCURRENT_PIPELINES": "1",
    "RMS_MAX_CONTEXT_FILE_BYTES": "131072",
    "RMS_MAX_CONTEXT_FILES": "8",
    "RMS_MAX_CONTEXT_TOTAL_BYTES": "524288",
    "RMS_MAX_INDEXED_FILES": "20000",
    "RMS_MAX_ISSUES_PER_RUN": "1",
    "RMS_MIN_FREE_DISK_MB": "256",
    "RMS_OPENROUTER_ALLOW_FALLBACKS": "true",
    "RMS_OPENROUTER_CONNECT_TIMEOUT_SECONDS": "5",
    "RMS_OPENROUTER_DATA_COLLECTION": "deny",
    "RMS_OPENROUTER_KEEPALIVE_EXPIRY_SECONDS": "30",
    "RMS_OPENROUTER_LOG_COST": "true",
    "RMS_OPENROUTER_LOG_PROMPTS": "false",
    "RMS_OPENROUTER_LOG_USAGE": "true",
    "RMS_OPENROUTER_MAX_CONNECTIONS": "2",
    "RMS_OPENROUTER_MAX_KEEPALIVE_CONNECTIONS": "1",
    "RMS_OPENROUTER_MAX_RETRIES": "0",
    "RMS_OPENROUTER_POOL_TIMEOUT_SECONDS": "5",
    "RMS_OPENROUTER_PROVIDER_SORT": "price",
    "RMS_OPENROUTER_READ_TIMEOUT_SECONDS": "90",
    "RMS_OPENROUTER_RETRY_BASE_SECONDS": "1",
    "RMS_OPENROUTER_RETRY_MAX_SECONDS": "8",
    "RMS_OPENROUTER_WRITE_TIMEOUT_SECONDS": "30",
    "RMS_PORT": "8000",
    "RMS_PRIMARY_MAX_TOKENS": "6144",
    "RMS_PRIMARY_TEMPERATURE": "0",
    "RMS_PUSH_ENABLED": "false",
    "RMS_QA_BRANCH_PREFIX": "rms-qa/",
    "RMS_READINESS_CACHE_SECONDS": "60",
    "RMS_REPORT_DIR": "/tmp/rams-reports",
    "RMS_REPORT_MAX_BYTES": "4194304",
    "RMS_REPORT_PREFIX": "qa-suite/reports",
    "RMS_REPO_BASE_DIR": "/tmp/rams-repos",
    "RMS_REPO_BOOTSTRAP_ENABLED": "true",
    "RMS_REVERT_ON_VALIDATION_FAILURE": "true",
    "RMS_SECONDARY_MAX_TOKENS": "3072",
    "RMS_SHUTDOWN_GRACE_SECONDS": "25",
    "RMS_SINGLE_WORKER_MODE": "true",
    "RMS_TEMP_CLEANUP_ENABLED": "true",
    "RMS_TEMP_MAX_AGE_HOURS": "24",
    "RMS_TOP_P": "0.9",
    "RMS_TRIAGE_MAX_TOKENS": "128",
    "RMS_TRIAGE_TEMPERATURE": "0",
    "RMS_VALIDATE_AFTER_EACH_TASK": "true",
    "RMS_VALIDATION_OUTPUT_MAX_BYTES": "131072",
    "RMS_VALIDATION_OUTPUT_MAX_LINES": "120",
    "RMS_VALIDATION_TIMEOUT_SECONDS": "240",
    "RMS_WEBSITE_REPO_BRANCH": "main",
    "RMS_WEBSITE_REPO_PATH": "/tmp/rams-repos/website",
    "RMS_WEBSITE_VALIDATION_COMMANDS": "python3 scripts/inject_partials.py --validate && python3 scripts/sync_redirects.py --check && python3 scripts/check_crawlers.py",
    "UVICORN_WORKERS": "1",
    "UV_THREADPOOL_SIZE": "2",
    "WEB_CONCURRENCY": "1",
}


def _parse_koyeb_bindings() -> dict[str, str]:
    bindings: dict[str, str] = {}
    for raw in (ROOT / "RAMS-KOYEB-PRODUCTION-ENV.txt").read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        bindings[key] = value
    return bindings


def _parse_docker_env() -> dict[str, str]:
    logical_lines: list[str] = []
    pending = ""
    for raw in (ROOT / "Dockerfile").read_text().splitlines():
        stripped = raw.strip()
        if pending:
            pending += " " + stripped
        else:
            pending = stripped
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        logical_lines.append(pending)
        pending = ""

    result: dict[str, str] = {}
    for line in logical_lines:
        if not line.startswith("ENV "):
            continue
        for token in shlex.split(line[4:]):
            key, value = token.split("=", 1)
            result[key] = value
    return result


def test_koyeb_manifest_contains_only_required_sensitive_bindings() -> None:
    assert _parse_koyeb_bindings() == EXPECTED_KOYEB_BINDINGS


def test_non_secret_production_configuration_is_baked_into_image() -> None:
    docker_env = _parse_docker_env()
    assert {key: docker_env.get(key) for key in EXPECTED_IMAGE_DEFAULTS} == EXPECTED_IMAGE_DEFAULTS


def test_image_defaults_plus_sensitive_bindings_form_complete_production_config() -> None:
    import os
    from unittest.mock import patch

    from repo_mgmt.config import Settings

    env = {
        **_parse_docker_env(),
        "OPENROUTER_API_KEY": "test-openrouter-key",
        "R2_ACCESS_KEY_ID": "test-r2-key",
        "R2_SECRET_ACCESS_KEY": "test-r2-secret",
        "RMS_API_KEY": "test-rams-api-key",
        "RMS_GITHUB_TOKEN": "test-github-token",
        "RMS_WEBSITE_REPO_URL": "https://github.com/example/website.git",
        "RMS_AIMS_REPO_URL": "https://github.com/example/aims.git",
    }
    with patch.dict(os.environ, env, clear=True):
        cfg = Settings()

    assert cfg.rms_environment == "production"
    assert cfg.live_write_permitted is True
    assert cfg.rms_push_enabled is False
    assert cfg.rms_create_pr is False
    assert cfg.rms_max_issues_per_run == 1
    assert cfg.openrouter_primary_model == "anthropic/claude-sonnet-4-6"
