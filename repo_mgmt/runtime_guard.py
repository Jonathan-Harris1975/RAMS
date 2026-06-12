"""Small local-runtime guards for RAMS ephemeral deployments."""

from __future__ import annotations

import time
from pathlib import Path

_REPORT_PATTERNS = ("dry-run-*-report.json", "fallback-*-report.json")


def cleanup_stale_reports(report_dir: Path, max_age_hours: int) -> int:
    """Delete only stale RAMS-owned local report files and return the count."""
    root = report_dir.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return 0
    cutoff = time.time() - (max_age_hours * 3600)
    deleted = 0
    for pattern in _REPORT_PATTERNS:
        for path in root.glob(pattern):
            try:
                resolved = path.resolve()
                resolved.relative_to(root)
                if resolved.is_file() and resolved.stat().st_mtime < cutoff:
                    resolved.unlink()
                    deleted += 1
            except (OSError, ValueError):
                continue
    return deleted
