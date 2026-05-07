"""
Repository index builder for the Repo Management Suite.

Scans a local repository and returns a structured index of its contents,
respecting .gitignore via pathspec.

Supports two target types:
  node    — for seo-aeo-geo (Node/Next.js repo)
  static  — for mobile-ux / on-brand (static HTML/CSS site)
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import pathspec as _pathspec_mod  # type: ignore[import-untyped]
    _HAS_PATHSPEC = True
except ImportError:
    _HAS_PATHSPEC = False
    logger.warning("repo_index: pathspec not installed — .gitignore patterns will not be applied")


def _load_gitignore(repo_root: Path) -> Any:
    """Load and parse .gitignore from *repo_root*, or return None."""
    gitignore_path = repo_root / ".gitignore"
    if not _HAS_PATHSPEC or not gitignore_path.is_file():
        return None
    patterns = gitignore_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return _pathspec_mod.PathSpec.from_lines("gitwildmatch", patterns)


def _walk(repo_root: Path, spec: Any) -> list[str]:
    """
    Walk *repo_root* and return repo-relative paths, honouring .gitignore.

    Args:
        repo_root: Absolute path to the repository root.
        spec: Compiled pathspec.PathSpec, or None to skip gitignore filtering.

    Returns:
        Sorted list of repo-relative path strings.
    """
    results: list[str] = []
    for path in repo_root.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(repo_root).as_posix()
        # Always skip hidden dirs like .git
        parts = path.relative_to(repo_root).parts
        if any(p.startswith(".") for p in parts):
            continue
        if spec is not None and spec.match_file(rel):
            continue
        results.append(rel)
    return sorted(results)


def _recent_commits(repo_root: Path, n: int = 10) -> list[str]:
    """Return the last *n* commit oneline summaries via git log."""
    try:
        result = subprocess.run(
            ["git", "log", f"--oneline", f"-{n}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip().splitlines()
    except Exception:
        return []


def build_node_index(repo_root: Path) -> dict[str, Any]:
    """
    Build an index for a Node.js/Next.js target repository.

    Args:
        repo_root: Absolute path to the repo root.

    Returns:
        Dict with keys: file_list, by_extension, route_strings,
        package_scripts, recent_commits.
    """
    spec = _load_gitignore(repo_root)
    file_list = _walk(repo_root, spec)

    by_ext: dict[str, list[str]] = {}
    for f in file_list:
        ext = Path(f).suffix or "(none)"
        by_ext.setdefault(ext, []).append(f)

    # Route strings: files under pages/ or app/ directories
    route_strings = [
        f for f in file_list
        if f.startswith("pages/") or f.startswith("app/")
    ]

    # package.json scripts
    pkg_path = repo_root / "package.json"
    package_scripts: dict[str, str] = {}
    if pkg_path.is_file():
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            package_scripts = pkg.get("scripts", {})
        except json.JSONDecodeError:
            pass

    return {
        "file_list": file_list,
        "by_extension": by_ext,
        "route_strings": route_strings,
        "package_scripts": package_scripts,
        "recent_commits": _recent_commits(repo_root),
    }


def build_static_index(repo_root: Path) -> dict[str, Any]:
    """
    Build an index for a static HTML/CSS target repository.

    Args:
        repo_root: Absolute path to the repo root.

    Returns:
        Dict with keys: file_list, by_extension, html_pages,
        css_files, partial_files, recent_commits.
    """
    spec = _load_gitignore(repo_root)
    file_list = _walk(repo_root, spec)

    by_ext: dict[str, list[str]] = {}
    for f in file_list:
        ext = Path(f).suffix or "(none)"
        by_ext.setdefault(ext, []).append(f)

    html_pages = [f for f in file_list if f.endswith(".html")]
    css_files = [f for f in file_list if f.endswith(".css")]
    partial_files = [f for f in file_list if "partial" in f.lower()]

    return {
        "file_list": file_list,
        "by_extension": by_ext,
        "html_pages": html_pages,
        "css_files": css_files,
        "partial_files": partial_files,
        "recent_commits": _recent_commits(repo_root),
    }


def build(repo_root: Path, target_type: str = "static") -> dict[str, Any]:
    """
    Build a repository index of the given *target_type*.

    Args:
        repo_root: Absolute path to the repository root.
        target_type: 'node' or 'static'.

    Returns:
        Index dict for the specified target type.
    """
    if target_type == "node":
        return build_node_index(repo_root)
    return build_static_index(repo_root)
