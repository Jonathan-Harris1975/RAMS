"""Lane 1 repository-local capability metadata for RAMS reports."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from repo_mgmt.local_skills import rams_local_skills_contract


def build_lane1_skills_baseline(*, pipeline_id: str | None = None) -> dict[str, Any]:
    """Return deterministic native-capability governance metadata.

    The legacy function name is retained for report compatibility. Its source is
    now the versioned ``config/skills`` directory; RAMS never fetches a shared
    manifest or executes capability metadata as code.
    """

    contract = rams_local_skills_contract(pipeline_id=pipeline_id)
    return {
        "batch": (
            "Batch 1 - Search visibility baseline"
            if pipeline_id == "seo-aeo-geo"
            else "Lane 1 native capabilities"
        ),
        "mode": contract["mode"],
        "generatedAt": datetime.now(tz=timezone.utc).isoformat(),
        "pipeline": pipeline_id,
        "schemaVersion": "rams-local-v1",
        "lane": "Lane 1 - Autonomous",
        "repoSideSetup": True,
        "externalInstallRequired": False,
        "sharedBucketRequired": False,
        "localAgentsFolderRequired": False,
        "skillCount": contract["skillCount"],
        "manifestControlled": True,
        "skills": contract["skills"],
        "localSkillCatalogue": contract,
        "governance": contract["governance"],
    }
