"""Tests for protected path enforcement at normaliser and applier gates."""

from __future__ import annotations

import pytest
from pathlib import Path

from repo_mgmt.patch_protocol import is_protected, ProtectedPathError
from repo_mgmt.patch_applier import apply, PROTECTED_PATHS


MOBILE_UX_PROTECTED = PROTECTED_PATHS["mobile-ux"]
ON_BRAND_PROTECTED = PROTECTED_PATHS["on-brand"]


# ── is_protected assertions matching design brief ──────────────────────────


def test_blog_posts_dir_protected() -> None:
    assert is_protected("blog/posts/2026-W16/index.html", MOBILE_UX_PROTECTED) is True


def test_blog_posts_json_protected() -> None:
    assert is_protected("blog/posts.json", MOBILE_UX_PROTECTED) is True


def test_transcripts_protected() -> None:
    assert is_protected("transcripts/ep-42/index.html", MOBILE_UX_PROTECTED) is True


def test_site_css_not_protected() -> None:
    assert is_protected("assets/css/site.css", MOBILE_UX_PROTECTED) is False


def test_header_partial_not_protected() -> None:
    assert is_protected("assets/partials/header.html", MOBILE_UX_PROTECTED) is False


# ── patch_applier gate ─────────────────────────────────────────────────────


def test_applier_raises_for_mobile_ux_blog(tmp_path: Path) -> None:
    (tmp_path / "blog" / "posts").mkdir(parents=True)
    f = tmp_path / "blog" / "posts" / "index.html"
    f.write_text("<p>content</p>", encoding="utf-8")
    patch = {
        "patchProtocol": "AnchorPatch/v1",
        "changes": [
            {
                "file": "blog/posts/index.html",
                "operation": "replace",
                "anchorBefore": "<p>content</p>",
                "find": "<p>content</p>",
                "replace": "<p>new</p>",
                "rationale": "test",
            }
        ],
    }
    with pytest.raises(ProtectedPathError):
        apply(patch, tmp_path, dry_run=False, pipeline_id="mobile-ux")


def test_applier_raises_for_on_brand_blog_content(tmp_path: Path) -> None:
    (tmp_path / "blog" / "posts").mkdir(parents=True)
    f = tmp_path / "blog" / "posts" / "index.html"
    f.write_text("<p>content</p>", encoding="utf-8")
    patch = {
        "patchProtocol": "AnchorPatch/v1",
        "changes": [
            {
                "file": "blog/posts/index.html",
                "operation": "replace",
                "anchorBefore": "<p>content</p>",
                "find": "<p>content</p>",
                "replace": "<p>new</p>",
                "rationale": "test",
            }
        ],
    }
    # On-brand council findings are future guidance by default; content rewrites stay protected.
    with pytest.raises(ProtectedPathError):
        apply(patch, tmp_path, dry_run=False, pipeline_id="on-brand")
    assert f.read_text(encoding="utf-8") == "<p>content</p>"
