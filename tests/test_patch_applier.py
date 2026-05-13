"""Tests for repo_mgmt.patch_applier - operations, safety gates, and atomic writes."""

from __future__ import annotations

import pytest
from pathlib import Path

from repo_mgmt.patch_applier import apply
from repo_mgmt.patch_protocol import PathTraversalError, ProtectedPathError


def _make_patch(
    file: str,
    operation: str,
    find: str = "",
    replace: str = "",
    anchor: str = "",
) -> dict:
    return {
        "patchProtocol": "AnchorPatch/v1",
        "changes": [
            {
                "file": file,
                "operation": operation,
                "anchorBefore": anchor,
                "find": find,
                "replace": replace,
                "rationale": "test",
            }
        ],
    }


def test_replace_operation(tmp_path: Path) -> None:
    f = tmp_path / "index.html"
    f.write_text("<title>Old</title>", encoding="utf-8")
    patch = _make_patch(
        "index.html", "replace", find="Old", replace="New", anchor="<title>Old</title>"
    )
    apply(patch, tmp_path, dry_run=False, pipeline_id="on-brand")
    assert f.read_text(encoding="utf-8") == "<title>New</title>"


def test_replace_double_find_raises(tmp_path: Path) -> None:
    f = tmp_path / "page.html"
    f.write_text("anchor foo foo", encoding="utf-8")
    patch = _make_patch(
        "page.html", "replace", find="foo", replace="bar", anchor="anchor"
    )
    with pytest.raises(Exception, match="unique"):
        apply(patch, tmp_path, dry_run=False)


def test_replace_find_missing_raises(tmp_path: Path) -> None:
    f = tmp_path / "page.html"
    f.write_text("anchor hello world", encoding="utf-8")
    patch = _make_patch(
        "page.html", "replace", find="NOMATCH", replace="x", anchor="anchor"
    )
    with pytest.raises(Exception, match="not found"):
        apply(patch, tmp_path, dry_run=False)


def test_insert_after_operation(tmp_path: Path) -> None:
    f = tmp_path / "style.css"
    f.write_text("body { color: red; }", encoding="utf-8")
    patch = _make_patch(
        "style.css",
        "insert_after",
        find="body { color: red; }",
        replace="\nh1 { color: blue; }",
        anchor="body { color: red; }",
    )
    apply(patch, tmp_path, dry_run=False)
    assert "h1 { color: blue; }" in f.read_text(encoding="utf-8")


def test_delete_operation_removes_matched_text(tmp_path: Path) -> None:
    f = tmp_path / "old.txt"
    f.write_text("hello remove-me world", encoding="utf-8")
    patch = _make_patch(
        "old.txt", "delete", find="remove-me ", anchor="hello remove-me world"
    )
    apply(patch, tmp_path, dry_run=False)
    assert f.exists()
    assert f.read_text(encoding="utf-8") == "hello world"


def test_delete_requires_find(tmp_path: Path) -> None:
    f = tmp_path / "old.txt"
    f.write_text("hello world", encoding="utf-8")
    patch = _make_patch("old.txt", "delete", anchor="hello world")
    with pytest.raises(Exception, match="find"):
        apply(patch, tmp_path, dry_run=False)
    assert f.read_text(encoding="utf-8") == "hello world"


def test_delete_requires_anchor_before(tmp_path: Path) -> None:
    f = tmp_path / "old.txt"
    f.write_text("hello world", encoding="utf-8")
    patch = _make_patch("old.txt", "delete", find="world")
    with pytest.raises(Exception, match="anchorBefore"):
        apply(patch, tmp_path, dry_run=False)
    assert f.read_text(encoding="utf-8") == "hello world"


def test_delete_empty_find_cannot_wipe_file(tmp_path: Path) -> None:
    f = tmp_path / "old.txt"
    f.write_text("keep this file", encoding="utf-8")
    patch = _make_patch("old.txt", "delete", find="", anchor="keep this file")
    with pytest.raises(Exception, match="find"):
        apply(patch, tmp_path, dry_run=False)
    assert f.exists()
    assert f.read_text(encoding="utf-8") == "keep this file"


def test_delete_double_find_raises(tmp_path: Path) -> None:
    f = tmp_path / "old.txt"
    f.write_text("anchor token token", encoding="utf-8")
    patch = _make_patch("old.txt", "delete", find="token", anchor="anchor")
    with pytest.raises(Exception, match="unique"):
        apply(patch, tmp_path, dry_run=False)
    assert f.read_text(encoding="utf-8") == "anchor token token"


def test_delete_missing_anchor_fails_before_modification(tmp_path: Path) -> None:
    f = tmp_path / "old.txt"
    f.write_text("hello world", encoding="utf-8")
    patch = _make_patch("old.txt", "delete", find="world", anchor="missing-anchor")
    with pytest.raises(Exception, match="anchorBefore"):
        apply(patch, tmp_path, dry_run=False)
    assert f.read_text(encoding="utf-8") == "hello world"


def test_dry_run_no_write(tmp_path: Path) -> None:
    f = tmp_path / "index.html"
    f.write_text("original", encoding="utf-8")
    patch = _make_patch(
        "index.html", "replace", find="original", replace="changed", anchor="original"
    )
    apply(patch, tmp_path, dry_run=True)
    assert f.read_text(encoding="utf-8") == "original"


def test_path_traversal_raises(tmp_path: Path) -> None:
    patch = _make_patch(
        "../../etc/passwd", "replace", find="root", replace="pwned", anchor="root"
    )
    with pytest.raises(PathTraversalError):
        apply(patch, tmp_path, dry_run=False)


def test_protected_mobile_ux_blog(tmp_path: Path) -> None:
    (tmp_path / "blog" / "posts" / "2026-W16").mkdir(parents=True)
    f = tmp_path / "blog" / "posts" / "2026-W16" / "index.html"
    f.write_text("<h1>Post</h1>", encoding="utf-8")
    patch = _make_patch(
        "blog/posts/2026-W16/index.html",
        "replace",
        find="<h1>Post</h1>",
        replace="<h1>New</h1>",
        anchor="<h1>Post</h1>",
    )
    with pytest.raises(ProtectedPathError):
        apply(patch, tmp_path, dry_run=False, pipeline_id="mobile-ux")


def test_not_protected_on_brand_blog(tmp_path: Path) -> None:
    """on-brand has no protected paths at applier level - blog edits must succeed."""
    (tmp_path / "blog" / "posts" / "2026-W16").mkdir(parents=True)
    f = tmp_path / "blog" / "posts" / "2026-W16" / "index.html"
    f.write_text("<h1>Post</h1>", encoding="utf-8")
    patch = _make_patch(
        "blog/posts/2026-W16/index.html",
        "replace",
        find="<h1>Post</h1>",
        replace="<h1>New</h1>",
        anchor="<h1>Post</h1>",
    )
    apply(patch, tmp_path, dry_run=False, pipeline_id="on-brand")
    assert f.read_text(encoding="utf-8") == "<h1>New</h1>"


def test_no_tmp_file_left_after_write(tmp_path: Path) -> None:
    f = tmp_path / "a.html"
    f.write_text("hello", encoding="utf-8")
    patch = _make_patch(
        "a.html", "replace", find="hello", replace="world", anchor="hello"
    )
    apply(patch, tmp_path, dry_run=False)
    tmp_files = list(tmp_path.glob("*.rms.tmp"))
    assert tmp_files == [], f"Unexpected .rms.tmp files: {tmp_files}"
