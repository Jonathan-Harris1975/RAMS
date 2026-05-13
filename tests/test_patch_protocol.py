"""Tests for repo_mgmt.patch_protocol — AnchorPatch/v1 schema and path safety."""

from __future__ import annotations

import pytest

from repo_mgmt.patch_protocol import (
    PROTOCOL_VERSION,
    PatchSchemaError,
    is_protected,
    validate_patch,
)

# ── is_protected ───────────────────────────────────────────────────────────

MOBILE_UX_PROTECTED: frozenset[str] = frozenset(
    [
        "blog/posts/",
        "blog/posts.json",
        "transcripts/",
        "data/podcast-episodes.json",
        "assets/js/podcast-transcripts.min.js",
        "functions/transcripts/",
    ]
)


def test_is_protected_blog_posts_dir() -> None:
    assert is_protected("blog/posts/2026-W16/index.html", MOBILE_UX_PROTECTED) is True


def test_is_protected_blog_posts_json() -> None:
    assert is_protected("blog/posts.json", MOBILE_UX_PROTECTED) is True


def test_is_protected_transcripts() -> None:
    assert is_protected("transcripts/ep-42/index.html", MOBILE_UX_PROTECTED) is True


def test_not_protected_css() -> None:
    assert is_protected("assets/css/site.css", MOBILE_UX_PROTECTED) is False


def test_not_protected_header_partial() -> None:
    assert is_protected("assets/partials/header.html", MOBILE_UX_PROTECTED) is False


def test_is_protected_exact_match() -> None:
    assert is_protected("blog/posts.json", MOBILE_UX_PROTECTED) is True


def test_is_protected_empty_set() -> None:
    assert is_protected("blog/posts/anything.html", frozenset()) is False


# ── validate_patch ─────────────────────────────────────────────────────────


def _valid_doc() -> dict:
    return {
        "patchProtocol": "AnchorPatch/v1",
        "changes": [
            {
                "file": "src/main.js",
                "operation": "replace",
                "anchorBefore": "function foo",
                "find": "const x = 1;",
                "replace": "const x = 2;",
                "rationale": "Fix constant",
            }
        ],
    }


def test_validate_patch_valid() -> None:
    doc = _valid_doc()
    result = validate_patch(doc)
    assert result["patchProtocol"] == PROTOCOL_VERSION


def test_validate_patch_wrong_protocol() -> None:
    doc = _valid_doc()
    doc["patchProtocol"] = "OtherProtocol/v2"
    with pytest.raises(PatchSchemaError, match="patchProtocol"):
        validate_patch(doc)


def test_validate_patch_not_dict() -> None:
    with pytest.raises(PatchSchemaError):
        validate_patch([1, 2, 3])  # type: ignore[arg-type]


def test_validate_patch_missing_changes() -> None:
    doc = {"patchProtocol": "AnchorPatch/v1", "changes": "not-a-list"}
    with pytest.raises(PatchSchemaError, match="array"):
        validate_patch(doc)


def test_validate_patch_invalid_operation() -> None:
    doc = _valid_doc()
    doc["changes"][0]["operation"] = "create"
    with pytest.raises(PatchSchemaError, match="operation"):
        validate_patch(doc)


def test_validate_patch_missing_find_for_replace() -> None:
    doc = _valid_doc()
    doc["changes"][0]["find"] = ""
    with pytest.raises(PatchSchemaError, match="find"):
        validate_patch(doc)


def test_validate_patch_delete_requires_find_and_anchor() -> None:
    doc = {
        "patchProtocol": "AnchorPatch/v1",
        "changes": [
            {
                "file": "old.txt",
                "operation": "delete",
                "anchorBefore": "anchor",
                "find": "remove me",
                "replace": "",
                "rationale": "remove text",
            }
        ],
    }
    validate_patch(doc)


def test_validate_patch_delete_without_find_rejected() -> None:
    doc = {
        "patchProtocol": "AnchorPatch/v1",
        "changes": [
            {
                "file": "old.txt",
                "operation": "delete",
                "anchorBefore": "anchor",
                "find": "",
                "replace": "",
                "rationale": "remove text",
            }
        ],
    }
    with pytest.raises(PatchSchemaError, match="find"):
        validate_patch(doc)


def test_validate_patch_delete_without_anchor_rejected() -> None:
    doc = {
        "patchProtocol": "AnchorPatch/v1",
        "changes": [
            {
                "file": "old.txt",
                "operation": "delete",
                "anchorBefore": "",
                "find": "remove me",
                "replace": "",
                "rationale": "remove text",
            }
        ],
    }
    with pytest.raises(PatchSchemaError, match="anchorBefore"):
        validate_patch(doc)


def test_validate_patch_replace_without_anchor_rejected() -> None:
    doc = _valid_doc()
    doc["changes"][0]["anchorBefore"] = ""
    with pytest.raises(PatchSchemaError, match="anchorBefore"):
        validate_patch(doc)
