"""Phase 5 native-capability metadata for growth and accessibility governance."""

from __future__ import annotations

from typing import Any

from repo_mgmt.local_skills import rams_local_skills_contract

PHASE5_CAPABILITIES: dict[str, list[str]] = {
    "ebookConversion": ["RAMS-sk001", "RAMS-sk002", "RAMS-sk003"],
    "visualSocial": ["RAMS-sk001", "RAMS-sk002", "RAMS-sk003"],
    "accessibilityMobileUx": ["RAMS-sk001", "RAMS-sk002", "RAMS-sk003"],
}

PARKED_CAPABILITIES: dict[str, str] = {
    "paid-ads": "Parked: fully organic growth only for now.",
    "analytics-tracking": "Deferred until Metricool and Google Analytics are active again.",
    "programmatic-seo": "Parked because existing search-visibility pipelines own this lane.",
    "cold-email": "Parked because the existing outreach pipeline owns this lane.",
    "lead-magnets": "Parked because it is not currently required.",
}


def phase5_skills_summary(pipeline: str | None = None) -> dict[str, Any]:
    """Return Phase 5 metadata using only native RAMS capabilities.

    The legacy function name is retained for report compatibility.
    """

    active = PHASE5_CAPABILITIES
    if pipeline == "mobile-ux":
        active = {"accessibilityMobileUx": PHASE5_CAPABILITIES["accessibilityMobileUx"]}
    catalogue = rams_local_skills_contract(pipeline_id=pipeline)
    return {
        "phase": "5A/5B/5C",
        "mode": "repository-local-native with organic-only automation and fail-closed gates",
        "activeCapabilities": active,
        "parkedCapabilities": PARKED_CAPABILITIES,
        # Compatibility aliases retained for existing report consumers. These
        # values now contain repository-local RAMS capability IDs only.
        "activeSkills": active,
        "parkedSkills": PARKED_CAPABILITIES,
        "localAgentsFolderRequired": False,
        "manifestControlled": True,
        "sharedBucketRequired": False,
        "localSkillCatalogue": catalogue,
        "skillSource": catalogue,
        "policy": [
            "Ebook and social growth remain organic-only; paid-ad automation is parked.",
            "Metricool and Google Analytics integration is deferred until those tools are active again.",
            "Mobile UX uses verified accessibility evidence, while remediation remains approval-gated.",
            "RAMS uses native code and local capability records only; external skill bundles are disabled.",
        ],
    }
