"""
Shared pytest fixtures for the Repo Management Suite test suite.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

from repo_mgmt.config import Settings


# ── Minimal valid settings ─────────────────────────────────────────────────

VALID_ENV: dict[str, str] = {
    "R2_ENDPOINT": "https://test.r2.cloudflarestorage.com",
    "R2_ACCESS_KEY_ID": "test-key-id",
    "R2_SECRET_ACCESS_KEY": "test-secret",
    "R2_BUCKET_AUDITS": "audits",
    "OPENROUTER_API_KEY": "test-or-key",
    "OPENROUTER_PRIMARY_MODEL": "anthropic/claude-3-5-sonnet",
    "OPENROUTER_SECONDARY_MODEL": "anthropic/claude-3-haiku",
    "OPENROUTER_TRIAGE_MODEL": "openai/gpt-4o-mini",
    "RMS_SEO_REPO_PATH": "/tmp/fake-seo-repo",
    "RMS_WEBSITE_REPO_PATH": "/tmp/fake-website-repo",
    "RMS_DRY_RUN": "true",
    "RMS_PUSH_ENABLED": "false",
    "RMS_CREATE_PR": "false",
}


@pytest.fixture
def settings() -> Settings:
    """Return a fully-populated Settings object without reading from disk."""
    with patch.dict(os.environ, VALID_ENV, clear=False):
        return Settings()


@pytest.fixture
def mock_r2() -> MagicMock:
    """Return a MagicMock that mimics R2Client."""
    r2 = MagicMock()
    r2.get_object.return_value = b"{}"
    r2.put_object.return_value = None
    r2.object_exists.return_value = False
    return r2


@pytest.fixture
def mock_router() -> MagicMock:
    """Return a MagicMock that mimics ModelRouter."""
    router = MagicMock()
    router.complete.return_value = json.dumps({
        "taskId": "rms-on-brand-2026-05-05-001",
        "operations": [
            {
                "action": "replace",
                "path": "index.html",
                "search": "<title>Old</title>",
                "replacement": "<title>New</title>",
            }
        ],
    })
    router.triage.return_value = "STRUCTURAL"
    return router


@pytest.fixture
def sample_audit() -> dict:
    """Return a minimal audit snapshot dict with one solvable finding."""
    return {
        "generatedAt": "2026-05-05T00:00:00Z",
        "findings": [
            {
                "title": "Missing canonical tag",
                "description": "Page lacks a canonical link element in <head>.",
                "severity": "high",
                "confidence": 0.92,
                "fixClass": "html_fix",
                "affectedPaths": ["index.html"],
                "evidence": ["<head> has no <link rel='canonical'>"],
                "requiredOutcome": "Add <link rel='canonical' href='...'> to <head>",
                "sourceAudit": "on-brand",
            }
        ],
    }


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Create a minimal fake repo directory with an index.html file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "index.html").write_text(
        "<html><head><title>Old</title></head><body></body></html>",
        encoding="utf-8",
    )
    return repo
