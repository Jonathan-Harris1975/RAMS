"""Lane 1 autonomous skill metadata for RAMS reports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / ".agents" / "lane-1-skills.json"


def _load_registry() -> dict[str, Any]:
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schemaVersion": "missing", "governance": {}, "skills": []}
    return data if isinstance(data, dict) else {"schemaVersion": "invalid", "governance": {}, "skills": []}


def build_lane1_skills_baseline(*, pipeline_id: str | None = None) -> dict[str, Any]:
    """Return deterministic Lane 1 skill governance metadata for report serialisation."""
    registry = _load_registry()
    skills = [item for item in registry.get("skills", []) if isinstance(item, dict)]
    batch_counts: dict[str, int] = {}
    for skill in skills:
        batch = str(skill.get("batch") or "Unbatched")
        batch_counts[batch] = batch_counts.get(batch, 0) + 1
    return {
        "batch": "Batch 1 - Search visibility baseline" if pipeline_id == "seo-aeo-geo" else "Lane 1 autonomous skills",
        "mode": "reports-only",
        "generatedAt": datetime.now(tz=timezone.utc).isoformat(),
        "pipeline": pipeline_id,
        "schemaVersion": registry.get("schemaVersion"),
        "lane": "Lane 1 - Autonomous",
        "repoSideSetup": True,
        "externalInstallRequired": True,
        "skillCount": len(skills),
        "skills": [
            {
                "skill": skill.get("displayName") or skill.get("skill"),
                "slug": skill.get("skill"),
                "batch": skill.get("batch"),
                "priority": skill.get("priority"),
                "repository": skill.get("repository"),
                "ecosystemFit": skill.get("ecosystemFit"),
                "manualCheckpoint": skill.get("manualCheckpoint"),
            }
            for skill in skills
        ],
        "batchCounts": batch_counts,
        "governance": registry.get("governance") or {},
    }
