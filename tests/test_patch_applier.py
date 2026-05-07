"""Tests for repo_mgmt.patch_applier."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_mgmt.patch_applier import PatchApplyError, apply


def _plan(task_id: str, ops: list) -> dict:
    return {"taskId": task_id, "operations": ops}


class TestApplyReplace:
    def test_replace_modifies_file(self, tmp_path: Path) -> None:
        f = tmp_path / "page.html"
        f.write_text("<title>Old</title>", encoding="utf-8")
        plan = _plan("t1", [{"action": "replace", "path": "page.html",
                              "search": "<title>Old</title>",
                              "replacement": "<title>New</title>"}])
        apply(plan, tmp_path, dry_run=False)
        assert f.read_text(encoding="utf-8") == "<title>New</title>"

    def test_replace_dry_run_leaves_file_unchanged(self, tmp_path: Path) -> None:
        f = tmp_path / "page.html"
        original = "<title>Old</title>"
        f.write_text(original, encoding="utf-8")
        plan = _plan("t1", [{"action": "replace", "path": "page.html",
                              "search": "<title>Old</title>",
                              "replacement": "<title>New</title>"}])
        apply(plan, tmp_path, dry_run=True)
        assert f.read_text(encoding="utf-8") == original

    def test_replace_raises_if_search_not_found(self, tmp_path: Path) -> None:
        f = tmp_path / "page.html"
        f.write_text("<title>Something else</title>", encoding="utf-8")
        plan = _plan("t1", [{"action": "replace", "path": "page.html",
                              "search": "DOESNOTEXIST",
                              "replacement": "NEW"}])
        with pytest.raises(PatchApplyError, match="not found"):
            apply(plan, tmp_path, dry_run=False)

    def test_replace_raises_if_search_not_unique(self, tmp_path: Path) -> None:
        f = tmp_path / "page.html"
        f.write_text("foo foo", encoding="utf-8")
        plan = _plan("t1", [{"action": "replace", "path": "page.html",
                              "search": "foo",
                              "replacement": "bar"}])
        with pytest.raises(PatchApplyError, match="matches 2 locations"):
            apply(plan, tmp_path, dry_run=False)


class TestApplyCreate:
    def test_create_makes_new_file(self, tmp_path: Path) -> None:
        plan = _plan("t1", [{"action": "create", "path": "new.html",
                              "content": "<html></html>"}])
        apply(plan, tmp_path, dry_run=False)
        assert (tmp_path / "new.html").read_text() == "<html></html>"

    def test_create_dry_run_does_not_create(self, tmp_path: Path) -> None:
        plan = _plan("t1", [{"action": "create", "path": "new.html",
                              "content": "<html></html>"}])
        apply(plan, tmp_path, dry_run=True)
        assert not (tmp_path / "new.html").exists()

    def test_create_raises_if_file_exists(self, tmp_path: Path) -> None:
        (tmp_path / "existing.html").write_text("existing", encoding="utf-8")
        plan = _plan("t1", [{"action": "create", "path": "existing.html",
                              "content": "new"}])
        with pytest.raises(PatchApplyError, match="already exists"):
            apply(plan, tmp_path, dry_run=False)


class TestApplyDelete:
    def test_delete_removes_file(self, tmp_path: Path) -> None:
        f = tmp_path / "old.html"
        f.write_text("bye", encoding="utf-8")
        plan = _plan("t1", [{"action": "delete", "path": "old.html"}])
        apply(plan, tmp_path, dry_run=False)
        assert not f.exists()

    def test_delete_dry_run_leaves_file(self, tmp_path: Path) -> None:
        f = tmp_path / "old.html"
        f.write_text("bye", encoding="utf-8")
        plan = _plan("t1", [{"action": "delete", "path": "old.html"}])
        apply(plan, tmp_path, dry_run=True)
        assert f.exists()


class TestApplySecurity:
    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        plan = _plan("t1", [{"action": "delete", "path": "../../../etc/passwd"}])
        with pytest.raises(PatchApplyError, match="escapes repo root"):
            apply(plan, tmp_path, dry_run=False)

    def test_returns_list_of_modified_paths(self, tmp_path: Path) -> None:
        f = tmp_path / "index.html"
        f.write_text("ABC", encoding="utf-8")
        plan = _plan("t1", [{"action": "replace", "path": "index.html",
                              "search": "ABC", "replacement": "XYZ"}])
        result = apply(plan, tmp_path, dry_run=False)
        assert result == ["index.html"]
