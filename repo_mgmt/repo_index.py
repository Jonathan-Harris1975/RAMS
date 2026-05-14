"""Repository index builder with Node/Express route discovery."""

from __future__ import annotations

import json
import logging
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


def _load_gitignore(repo_root: Path) -> Any:
    """Load .gitignore as a PathSpec object or a minimal fallback list."""
    gitignore = repo_root / ".gitignore"
    if not gitignore.is_file():
        return None
    patterns = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
    if _HAS_PATHSPEC:
        return _pathspec_mod.PathSpec.from_lines("gitwildmatch", patterns)
    return [
        pattern.strip().rstrip("/")
        for pattern in patterns
        if pattern.strip() and not pattern.lstrip().startswith("#")
    ]


def _ignored(rel: str, spec: Any) -> bool:
    """Return True when a repo-relative path is ignored by the loaded spec."""
    if spec is None:
        return False
    if hasattr(spec, "match_file"):
        return bool(spec.match_file(rel))
    return any(
        rel == pattern or rel.startswith(pattern + "/") or Path(rel).name == pattern
        for pattern in spec
    )


def _walk(repo_root: Path, spec: Any) -> list[str]:
    """Return sorted, non-hidden, non-ignored file paths under repo_root."""
    out: list[str] = []
    for path in repo_root.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(repo_root).as_posix()
        parts = path.relative_to(repo_root).parts
        if any(part.startswith(".") for part in parts):
            continue
        if _ignored(rel, spec):
            continue
        out.append(rel)
    return sorted(out)


def _recent_commits(repo_root: Path, n: int = 10) -> list[str]:
    """Return up to *n* recent one-line commit summaries, or [] outside Git."""
    try:
        return (
            subprocess.run(
                ["git", "log", "--oneline", f"-{n}"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            .stdout.strip()
            .splitlines()
        )
    except Exception:
        return []


def _discover_routes(repo_root: Path, file_list: list[str]) -> list[str]:
    """Discover Express-like route strings from common route source files."""
    routes = {path for path in file_list if path.startswith(("pages/", "app/"))}
    candidates: list[str] = []
    for rel in file_list:
        rel_path = Path(rel)
        name = rel_path.name.lower()
        if rel in {"server.js", "server.ts"}:
            candidates.append(rel)
        elif rel.startswith("routes/") and rel_path.suffix in {
            ".js",
            ".mjs",
            ".cjs",
            ".ts",
        }:
            candidates.append(rel)
        elif rel.startswith("audits/routes/") and rel_path.suffix in {
            ".js",
            ".mjs",
            ".cjs",
            ".ts",
        }:
            candidates.append(rel)
        elif (
            rel.startswith("services/")
            and rel_path.suffix in {".js", ".mjs", ".cjs", ".ts"}
            and ("route" in name or name in {"server.js", "index.js", "app.js"})
        ):
            candidates.append(rel)
    for rel in candidates:
        text = (repo_root / rel).read_text(encoding="utf-8", errors="replace")
        routes.update(match.group(1) for match in _EXPRESS_RE.finditer(text))
    return sorted(routes)


def build_node_index(repo_root: Path) -> dict[str, Any]:
    """Build an index for the Node/Express target repository."""
    spec = _load_gitignore(repo_root)
    files = _walk(repo_root, spec)
    by_extension: dict[str, list[str]] = {}
    for rel in files:
        by_extension.setdefault(Path(rel).suffix or "(none)", []).append(rel)
    scripts: dict[str, Any] = {}
    package_json = repo_root / "package.json"
    if package_json.is_file():
        try:
            scripts = json.loads(package_json.read_text(encoding="utf-8")).get(
                "scripts", {}
            )
        except json.JSONDecodeError:
            logger.warning("repo_index: package.json is not valid JSON: %s", package_json)
    return {
        "file_list": files,
        "by_extension": by_extension,
        "route_strings": _discover_routes(repo_root, files),
        "package_scripts": scripts,
        "recent_commits": _recent_commits(repo_root),
    }


def build_static_index(repo_root: Path) -> dict[str, Any]:
    """Build an index for the static website target repository."""
    spec = _load_gitignore(repo_root)
    files = _walk(repo_root, spec)
    by_extension: dict[str, list[str]] = {}
    for rel in files:
        by_extension.setdefault(Path(rel).suffix or "(none)", []).append(rel)
    return {
        "file_list": files,
        "by_extension": by_extension,
        "html_pages": [rel for rel in files if rel.endswith(".html")],
        "css_files": [rel for rel in files if rel.endswith(".css")],
        "partial_files": [rel for rel in files if "partial" in rel.lower()],
        "recent_commits": _recent_commits(repo_root),
    }


def build(repo_root: Path, target_type: str = "static") -> dict[str, Any]:
    """Build a node or static repository index based on target_type."""
    if target_type == "node":
        return build_node_index(repo_root)
    return build_static_index(repo_root)
