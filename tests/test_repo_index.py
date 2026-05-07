"""Tests for repo_mgmt.repo_index."""
from __future__ import annotations

from pathlib import Path

from repo_mgmt.repo_index import build, build_static_index, build_node_index


def _make_static_repo(tmp_path: Path) -> Path:
    (tmp_path / "assets" / "css").mkdir(parents=True)
    (tmp_path / "assets" / "partials").mkdir(parents=True)
    (tmp_path / "blog" / "posts").mkdir(parents=True)
    (tmp_path / "assets" / "css" / "site.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "assets" / "partials" / "header.html").write_text("<header/>", encoding="utf-8")
    (tmp_path / "index.html").write_text("<html/>", encoding="utf-8")
    (tmp_path / "blog" / "posts" / "post1.html").write_text("<article/>", encoding="utf-8")
    return tmp_path


def test_static_index_has_expected_keys(tmp_path: Path) -> None:
    _make_static_repo(tmp_path)
    index = build_static_index(tmp_path)
    for key in ("file_list", "by_extension", "html_pages", "css_files", "partial_files"):
        assert key in index, f"Missing key: {key}"


def test_static_index_html_pages(tmp_path: Path) -> None:
    _make_static_repo(tmp_path)
    index = build_static_index(tmp_path)
    html_pages = index["html_pages"]
    assert any("index.html" in p for p in html_pages)


def test_static_index_css_files(tmp_path: Path) -> None:
    _make_static_repo(tmp_path)
    index = build_static_index(tmp_path)
    assert any("site.css" in p for p in index["css_files"])


def test_build_dispatcher_static(tmp_path: Path) -> None:
    _make_static_repo(tmp_path)
    index = build(tmp_path, "static")
    assert "html_pages" in index


def test_build_dispatcher_node(tmp_path: Path) -> None:
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "index.tsx").write_text("export default () => null", encoding="utf-8")
    index = build(tmp_path, "node")
    assert "route_strings" in index
    assert any("pages/" in r for r in index["route_strings"])


def test_gitignore_respected(tmp_path: Path) -> None:
    _make_static_repo(tmp_path)
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / "dep.js").write_text("x", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    index = build_static_index(tmp_path)
    assert not any("node_modules" in f for f in index["file_list"])
