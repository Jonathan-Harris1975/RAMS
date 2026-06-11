"""Lane 1 central skill-pool metadata for RAMS reports."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from repo_mgmt.hive_skill_pool import rams_skill_pool_contract


def build_lane1_skills_baseline(*, pipeline_id: str | None = None) -> dict[str, Any]:
    """Return deterministic Lane 1 skill governance metadata for reports.

    Skills are no longer read from ``.agents`` or installed into RAMS. The RAMS
    repo consumes the HIVE shared skill pool through the RAMS R2 manifest in
    read-only mode. HIVE remains the central controller for discovery,
    orchestration and execution decisions.
    """
    contract = rams_skill_pool_contract(pipeline_id=pipeline_id)
    return {
        "batch": "Batch 1 - Search visibility baseline" if pipeline_id == "seo-aeo-geo" else "Lane 1 autonomous skills",
        "mode": contract["mode"],
        "generatedAt": datetime.now(tz=timezone.utc).isoformat(),
        "pipeline": pipeline_id,
        "schemaVersion": "central-r2-v1",
        "lane": "Lane 1 - Autonomous",
        "repoSideSetup": False,
        "externalInstallRequired": False,
        "centralSkillPoolRequired": True,
        "localAgentsFolderRequired": False,
        "skillCount": 0,
        "manifestControlled": True,
        "skills": [],
        "batchCounts": {},
        "centralSkillPool": contract,
        "governance": contract["governance"],
    }
