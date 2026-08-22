"""Memory-bounded repository index builder with route discovery."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_pathspec_mod: Any
try:
    import pathspec as _pathspec_mod

    _HAS_PATHSPEC = True
except ImportError:
    _pathspec_mod = None
    _HAS_PATHSPEC = False

_EXPRESS_RE = re.compile(
    r"(?:app|router|[A-Za-z0-9_]+Router)\s*\.\s*"
    r"(?:get|post|put|patch|delete|use|all)\s*\(\s*['\"]([^'\"]+)['\"]"
)
_BUILTIN_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".idea",
        ".vscode",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "coverage",
        ".next",
        ".cache",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
    }
)


def _load_gitignore(repo_root: Path) -> Any:
    gitignore = repo_root / ".gitignore"
    if not gitignore.is_file():
        return None
    patterns = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
    if _HAS_PATHSPEC:
        return _pathspec_mod.GitIgnoreSpec.from_lines(patterns)
    return [
        pattern.strip().rstrip("/")
        for pattern in patterns
        if pattern.strip() and not pattern.lstrip().startswith("#")
    ]


def _ignored(rel: str, spec: Any) -> bool:
    if spec is None:
        return False
    if hasattr(spec, "match_file"):
        return bool(spec.match_file(rel))
    return any(
        rel == pattern or rel.startswith(pattern + "/") or Path(rel).name == pattern
        for pattern in spec
    )


def _walk(repo_root: Path, spec: Any, max_files: int) -> tuple[list[str], bool]:
    """Return bounded sorted paths while pruning known heavy directories."""
    out: list[str] = []
    truncated = False
    for current, dirs, files in os.walk(repo_root):
        current_path = Path(current)
        rel_dir = current_path.relative_to(repo_root)
        dirs[:] = sorted(
            directory
            for directory in dirs
            if directory not in _BUILTIN_EXCLUDED_DIRS
            and not directory.startswith(".")
            and not _ignored(
                ((rel_dir / directory).as_posix() + "/").lstrip("./"), spec
            )
        )
        for filename in sorted(files):
            if filename.startswith("."):
                continue
            rel = (rel_dir / filename).as_posix().lstrip("./")
            if _ignored(rel, spec):
                continue
            out.append(rel)
            if len(out) >= max_files:
                truncated = True
                return out, truncated
    return out, truncated


def _recent_commits(
    repo_root: Path, n: int = 10, timeout_seconds: int = 10
) -> list[str]:
    try:
        return (
            subprocess.run(
                ["git", "log", "--oneline", f"-{n}"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            .stdout.strip()
            .splitlines()
        )
    except Exception:
        return []


def _discover_routes(
    repo_root: Path,
    file_list: list[str],
    max_source_file_bytes: int,
) -> list[str]:
    routes = {path for path in file_list if path.startswith(("pages/", "app/"))}
    candidates: list[str] = []
    for rel in file_list:
        rel_path = Path(rel)
        name = rel_path.name.lower()
        suffix_ok = rel_path.suffix in {".js", ".mjs", ".cjs", ".ts"}
        if rel in {"server.js", "server.ts"}:
            candidates.append(rel)
        elif rel.startswith("routes/") and suffix_ok:
            candidates.append(rel)
        elif rel.startswith("audits/routes/") and suffix_ok:
            candidates.append(rel)
        elif (
            rel.startswith("services/")
            and suffix_ok
            and ("route" in name or name in {"server.js", "index.js", "app.js"})
        ):
            candidates.append(rel)
    for rel in candidates:
        path = repo_root / rel
        try:
            if path.stat().st_size > max_source_file_bytes:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        routes.update(match.group(1) for match in _EXPRESS_RE.finditer(text))
    return sorted(routes)


def build_node_index(
    repo_root: Path,
    *,
    max_files: int = 20_000,
    max_source_file_bytes: int = 256 * 1024,
    git_timeout_seconds: int = 10,
) -> dict[str, Any]:
    spec = _load_gitignore(repo_root)
    files, truncated = _walk(repo_root, spec, max_files)
    by_extension: dict[str, list[str]] = {}
    for rel in files:
        by_extension.setdefault(Path(rel).suffix or "(none)", []).append(rel)
    scripts: dict[str, Any] = {}
    package_json = repo_root / "package.json"
    if package_json.is_file() and package_json.stat().st_size <= max_source_file_bytes:
        try:
            scripts = json.loads(package_json.read_text(encoding="utf-8")).get(
                "scripts", {}
            )
        except json.JSONDecodeError:
            logger.warning(
                "repo_index: package.json is not valid JSON: %s", package_json
            )
    return {
        "file_list": files,
        "indexedFileCount": len(files),
        "truncated": truncated,
        "by_extension": by_extension,
        "route_strings": _discover_routes(repo_root, files, max_source_file_bytes),
        "package_scripts": scripts,
        "recent_commits": _recent_commits(
            repo_root, timeout_seconds=git_timeout_seconds
        ),
    }


def build_static_index(
    repo_root: Path,
    *,
    max_files: int = 20_000,
    git_timeout_seconds: int = 10,
) -> dict[str, Any]:
    spec = _load_gitignore(repo_root)
    files, truncated = _walk(repo_root, spec, max_files)
    by_extension: dict[str, list[str]] = {}
    for rel in files:
        by_extension.setdefault(Path(rel).suffix or "(none)", []).append(rel)
    return {
        "file_list": files,
        "indexedFileCount": len(files),
        "truncated": truncated,
        "by_extension": by_extension,
        "html_pages": [rel for rel in files if rel.endswith(".html")],
        "css_files": [rel for rel in files if rel.endswith(".css")],
        "partial_files": [rel for rel in files if "partial" in rel.lower()],
        "recent_commits": _recent_commits(
            repo_root, timeout_seconds=git_timeout_seconds
        ),
    }


def build(
    repo_root: Path,
    target_type: str = "static",
    *,
    max_files: int = 20_000,
    max_source_file_bytes: int = 256 * 1024,
    git_timeout_seconds: int = 10,
) -> dict[str, Any]:
    if target_type == "node":
        return build_node_index(
            repo_root,
            max_files=max_files,
            max_source_file_bytes=max_source_file_bytes,
            git_timeout_seconds=git_timeout_seconds,
        )
    return build_static_index(
        repo_root,
        max_files=max_files,
        git_timeout_seconds=git_timeout_seconds,
    )
