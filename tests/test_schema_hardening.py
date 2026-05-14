"""Strict schema and unsafe audit-data tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from repo_mgmt.issue_normaliser import normalise
from repo_mgmt.schemas import AnchorPatchModel, NormalisedIssueModel


def _issue() -> dict[str, object]:
    """Return a schema-valid NormalisedIssue payload."""
    return {
        "taskId": "rms-mobile-ux-2026-05-05-001",
        "pipeline": "mobile-ux",
        "sourceAudit": "mobile-ux",
        "classification": "code_fix",
        "severity": "high",
        "confidence": 0.8,
        "affectedPaths": ["index.html"],
        "evidence": [],
        "requiredOutcome": "Fix viewport",
        "allowedFixClass": "meta_fix",
        "validationCommands": ["true"],
        "status": "pending",
    }


def test_normalised_issue_rejects_invalid_severity() -> None:
    payload = _issue()
    payload["severity"] = "urgent"
    with pytest.raises(ValidationError):
        NormalisedIssueModel.model_validate(payload)


def test_normalised_issue_rejects_confidence_outside_range() -> None:
    payload = _issue()
    payload["confidence"] = 1.5
    with pytest.raises(ValidationError):
        NormalisedIssueModel.model_validate(payload)


def test_normalised_issue_rejects_path_traversal() -> None:
    payload = _issue()
    payload["affectedPaths"] = ["../secret.txt"]
    with pytest.raises(ValidationError):
        NormalisedIssueModel.model_validate(payload)


def test_anchor_patch_rejects_unknown_operation() -> None:
    with pytest.raises(ValidationError):
        AnchorPatchModel.model_validate(
            {
                "patchProtocol": "AnchorPatch/v1",
                "changes": [
                    {
                        "file": "index.html",
                        "operation": "create",
                        "anchorBefore": "x",
                        "find": "x",
                        "replace": "y",
                        "rationale": "not allowed",
                    }
                ],
            }
        )


def test_invalid_audit_metadata_becomes_manual_review(settings) -> None:
    audit = {
        "findings": [
            {
                "title": "Bad audit payload",
                "description": "Path and severity are invalid",
                "severity": "urgent",
                "confidence": "not-a-number",
                "fixClass": "html_fix",
                "affectedPaths": ["index.html"],
                "evidence": [],
                "requiredOutcome": "Fix safely",
                "sourceAudit": "on-brand",
            }
        ]
    }
    issues = normalise(audit, "on-brand", "2026-05-05", settings)
    assert issues[0]["classification"] == "manual_review"
    assert issues[0]["severity"] == "low"
    assert issues[0]["confidence"] == 0.0


def test_unsafe_audit_path_becomes_skipped(settings) -> None:
    audit = {
        "findings": [
            {
                "title": "Unsafe path",
                "description": "Do not touch this",
                "severity": "high",
                "confidence": 0.9,
                "fixClass": "html_fix",
                "affectedPaths": ["../outside.html"],
                "evidence": [],
                "requiredOutcome": "Never write outside repo",
                "sourceAudit": "mobile-ux",
            }
        ]
    }
    issues = normalise(audit, "mobile-ux", "2026-05-05", settings)
    assert issues[0]["classification"] == "skipped"
    assert issues[0]["affectedPaths"] == []
    assert "traversal" in issues[0]["skipReason"]
