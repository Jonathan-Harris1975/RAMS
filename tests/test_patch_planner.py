"""Tests for repo_mgmt.patch_planner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from repo_mgmt.patch_planner import PatchPlanError, plan, _parse_plan


class TestParsePlan:
    def test_valid_replace_plan_parses(self) -> None:
        raw = json.dumps({
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
        result = _parse_plan(raw, "rms-on-brand-2026-05-05-001")
        assert result["taskId"] == "rms-on-brand-2026-05-05-001"
        assert len(result["operations"]) == 1

    def test_strips_markdown_fences(self) -> None:
        inner = json.dumps({
            "taskId": "t1",
            "operations": [{"action": "delete", "path": "foo.txt"}],
        })
        raw = f"```json\n{inner}\n```"
        result = _parse_plan(raw, "t1")
        assert result["operations"][0]["action"] == "delete"

    def test_raises_on_invalid_json(self) -> None:
        with pytest.raises(PatchPlanError, match="not valid JSON"):
            _parse_plan("not json {{{", "t1")

    def test_raises_on_empty_operations(self) -> None:
        raw = json.dumps({"taskId": "t1", "operations": []})
        with pytest.raises(PatchPlanError, match="no operations"):
            _parse_plan(raw, "t1")

    def test_raises_on_unknown_action(self) -> None:
        raw = json.dumps({
            "taskId": "t1",
            "operations": [{"action": "teleport", "path": "x.html"}],
        })
        with pytest.raises(PatchPlanError, match="unknown action"):
            _parse_plan(raw, "t1")

    def test_raises_on_replace_missing_search(self) -> None:
        raw = json.dumps({
            "taskId": "t1",
            "operations": [{"action": "replace", "path": "x.html", "replacement": "y"}],
        })
        with pytest.raises(PatchPlanError, match="missing 'search'"):
            _parse_plan(raw, "t1")

    def test_raises_on_create_missing_content(self) -> None:
        raw = json.dumps({
            "taskId": "t1",
            "operations": [{"action": "create", "path": "new.html"}],
        })
        with pytest.raises(PatchPlanError, match="missing 'content'"):
            _parse_plan(raw, "t1")


class TestPlan:
    def test_raises_if_classification_not_code_fix(
        self, settings, mock_router: MagicMock, tmp_repo: Path
    ) -> None:
        issue = {
            "taskId": "rms-on-brand-2026-05-05-001",
            "classification": "future_guidance",
            "affectedPaths": [],
            "evidence": [],
            "requiredOutcome": "",
            "allowedFixClass": "",
        }
        with pytest.raises(PatchPlanError, match="non-code_fix"):
            plan(issue, tmp_repo, "on-brand", settings, mock_router)

    def test_returns_valid_plan_on_success(
        self, settings, mock_router: MagicMock, tmp_repo: Path
    ) -> None:
        issue = {
            "taskId": "rms-on-brand-2026-05-05-001",
            "classification": "code_fix",
            "affectedPaths": ["index.html"],
            "evidence": ["<head> missing canonical"],
            "requiredOutcome": "Add canonical tag",
            "allowedFixClass": "html_fix",
        }
        result = plan(issue, tmp_repo, "on-brand", settings, mock_router)
        assert result["taskId"] == "rms-on-brand-2026-05-05-001"
        assert isinstance(result["operations"], list)
        assert len(result["operations"]) > 0

    def test_raises_on_model_failure(
        self, settings, mock_router: MagicMock, tmp_repo: Path
    ) -> None:
        mock_router.complete.side_effect = Exception("network error")
        issue = {
            "taskId": "rms-on-brand-2026-05-05-001",
            "classification": "code_fix",
            "affectedPaths": ["index.html"],
            "evidence": [],
            "requiredOutcome": "",
            "allowedFixClass": "html_fix",
        }
        with pytest.raises(PatchPlanError, match="LLM call failed"):
            plan(issue, tmp_repo, "on-brand", settings, mock_router)
