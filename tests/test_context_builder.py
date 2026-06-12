"""Tests for bounded RAMS context collection."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_mgmt.context_builder import load_context
from repo_mgmt.patch_protocol import PathTraversalError


def test_context_deduplicates_and_caps_file_count(tmp_path: Path) -> None:
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    context = load_context(
        ["a.txt", "a.txt", "b.txt", "c.txt"],
        tmp_path,
        max_files=2,
        max_file_bytes=100,
        max_total_bytes=100,
    )
    assert list(context) == ["a.txt", "b.txt"]


def test_context_respects_total_byte_budget(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a" * 6, encoding="utf-8")
    (tmp_path / "b.txt").write_text("b" * 6, encoding="utf-8")
    context = load_context(
        ["a.txt", "b.txt"],
        tmp_path,
        max_files=4,
        max_file_bytes=10,
        max_total_bytes=10,
    )
    assert context == {"a.txt": "a" * 6}


def test_context_skips_binary_and_oversized_files(tmp_path: Path) -> None:
    (tmp_path / "binary.bin").write_bytes(b"abc\x00def")
    (tmp_path / "large.txt").write_text("x" * 20, encoding="utf-8")
    assert (
        load_context(
            ["binary.bin", "large.txt"],
            tmp_path,
            max_files=4,
            max_file_bytes=10,
            max_total_bytes=100,
        )
        == {}
    )


def test_context_rejects_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    with pytest.raises(PathTraversalError):
        load_context(["../outside.txt"], tmp_path)
