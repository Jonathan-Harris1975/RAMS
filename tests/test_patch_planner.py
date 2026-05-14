from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_mgmt.patch_planner import (
    SYSTEM_PROMPT,
    PatchPlanError,
    _parse_plan,
    _validate_plan_scope,
    plan,
)
from repo_mgmt.patch_protocol import PathTraversalError


def _patch(changes=None, reason: str | None = "No bounded patch is safe"):
    if changes is not None:
        for change in changes:
            if isinstance(change, dict):
                change.setdefault("rationale", "test patch")
    doc = {
        "patchProtocol": "AnchorPatch/v1",
        "changes": changes if changes is not None else [],
    }
    if reason is not None:
        doc["reason"] = reason
    return doc


class TestParsePlan:
    def test_valid_anchor_patch_parses_directly(self) -> None:
        doc = _patch(
            [
                {
                    "file": "index.html",
                    "operation": "replace",
                    "anchorBefore": "<title>Old</title>",
                    "find": "Old",
                    "replace": "New",
                }
            ],
            reason=None,
        )
        assert _parse_plan(json.dumps(doc), "t") == doc

    def test_empty_changes_require_reason_and_are_safe_noop(self) -> None:
        parsed = _parse_plan(json.dumps(_patch([])), "t")
        assert parsed["changes"] == []
        assert parsed["reason"]

    def test_empty_changes_without_reason_rejected(self) -> None:
        with pytest.raises(PatchPlanError, match="reason"):
            _parse_plan(json.dumps(_patch([], reason=None)), "t")

    def test_rejects_markdown_fences_as_not_strict_json(self) -> None:
        with pytest.raises(PatchPlanError):
            _parse_plan("```json\n{}\n```", "t")

    def test_raises_on_invalid_json(self) -> None:
        with pytest.raises(PatchPlanError):
            _parse_plan("not json", "t")

    def test_raises_on_custom_operations_contract(self) -> None:
        with pytest.raises(PatchPlanError):
            _parse_plan(json.dumps({"taskId": "x", "operations": []}), "t")

    def test_raises_on_wrong_patch_protocol(self) -> None:
        with pytest.raises(PatchPlanError):
            _parse_plan(json.dumps({"patchProtocol": "Other/v1", "changes": []}), "t")

    def test_raises_on_unknown_operation(self) -> None:
        with pytest.raises(PatchPlanError):
            _parse_plan(json.dumps(_patch([{"file": "x", "operation": "create"}], None)), "t")

    def test_raises_on_replace_missing_find(self) -> None:
        with pytest.raises(PatchPlanError):
            _parse_plan(
                json.dumps(
                    _patch([{"file": "x", "operation": "replace", "replace": "x"}], None)
                ),
                "t",
            )


class TestPlan:
    def test_prompt_requires_direct_anchor_patch_contract(self) -> None:
        assert "AnchorPatch/v1" in SYSTEM_PROMPT
        assert "No prose, no markdown fences" in SYSTEM_PROMPT
        assert '{"patchProtocol":"AnchorPatch/v1","changes":[],"reason":"<why>"}' in SYSTEM_PROMPT

    def test_raises_if_classification_not_code_fix(
        self, settings, mock_router, tmp_repo
    ) -> None:
        with pytest.raises(PatchPlanError):
            plan(
                {
                    "taskId": "x",
                    "classification": "future_guidance",
                    "affectedPaths": [],
                },
                tmp_repo,
                "on-brand",
                settings,
                mock_router,
            )

    def test_returns_anchor_patch_on_success(self, settings, mock_router, tmp_repo) -> None:
        result = plan(
            {
                "taskId": "x",
                "classification": "code_fix",
                "affectedPaths": ["index.html"],
            },
            tmp_repo,
            "on-brand",
            settings,
            mock_router,
        )
        assert result["patchProtocol"] == "AnchorPatch/v1"
        assert "operations" not in result

    def test_raises_on_model_failure(self, settings, mock_router, tmp_repo) -> None:
        mock_router.complete.side_effect = RuntimeError("model down")
        with pytest.raises(PatchPlanError):
            plan(
                {
                    "taskId": "x",
                    "classification": "code_fix",
                    "affectedPaths": ["index.html"],
                },
                tmp_repo,
                "on-brand",
                settings,
                mock_router,
            )

    def test_read_side_sibling_prefix_traversal_blocked(
        self, settings, mock_router, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        sib = tmp_path / "repo-secret"
        sib.mkdir()
        (sib / "secret.txt").write_text("secret")
        with pytest.raises(PathTraversalError):
            plan(
                {
                    "taskId": "x",
                    "classification": "code_fix",
                    "affectedPaths": ["../repo-secret/secret.txt"],
                },
                repo,
                "on-brand",
                settings,
                mock_router,
            )

    def test_plan_scope_rejects_files_outside_affected_paths(self) -> None:
        with pytest.raises(PatchPlanError, match="affectedPaths"):
            _validate_plan_scope(
                _patch(
                    [
                        {
                            "file": "other.html",
                            "operation": "replace",
                            "anchorBefore": "x",
                            "find": "x",
                            "replace": "y",
                        }
                    ],
                    None,
                ),
                ["index.html"],
                "on-brand",
            )

    def test_plan_scope_rejects_protected_mobile_ux_path(self) -> None:
        with pytest.raises(PatchPlanError, match="protected"):
            _validate_plan_scope(
                _patch(
                    [
                        {
                            "file": "blog/posts/index.html",
                            "operation": "replace",
                            "anchorBefore": "x",
                            "find": "x",
                            "replace": "y",
                        }
                    ],
                    None,
                ),
                ["blog/posts/index.html"],
                "mobile-ux",
            )
