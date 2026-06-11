"""Phase 5 skill metadata for organic growth and accessibility governance."""

from __future__ import annotations

from typing import Any

from repo_mgmt.hive_skill_pool import rams_skill_pool_contract


PHASE5_SKILLS: dict[str, list[str]] = {
    "ebookConversion": [
        "copywriting",
        "copy-editing",
        "marketing-psychology",
        "product-marketing-context",
    ],
    "visualSocial": [
        "social-content",
        "ai-social-media-content",
        "social-media-carousel",
        "og-image-design",
        "ai-image-generation",
        "image-upscaling",
        "content-repurposing",
    ],
    "accessibilityMobileUx": ["accessibility-audit"],
}

PARKED_SKILLS: dict[str, str] = {
    "paid-ads": "Parked: fully organic growth only for now.",
    "analytics-tracking": "Deferred until Metricool and Google Analytics are set up again.",
    "programmatic-seo": "Parked because existing SEO pipelines and blogs cover this lane.",
    "cold-email": "Parked because the existing outreach pipeline owns this lane.",
    "lead-magnets": "Parked because it is not required at the moment.",
}


def phase5_skills_summary(pipeline: str | None = None) -> dict[str, Any]:
    """Return Phase 5 governance metadata for RAMS reports."""
    active = PHASE5_SKILLS
    if pipeline == "mobile-ux":
        active = {"accessibilityMobileUx": PHASE5_SKILLS["accessibilityMobileUx"]}

    return {
        "phase": "5A/5B/5C",
        "mode": "central-r2-read-only with organic-only automation and fail-closed gates",
        "activeSkills": active,
        "parkedSkills": PARKED_SKILLS,
        "localAgentsFolderRequired": False,
        "manifestControlled": True,
        "skillSource": rams_skill_pool_contract(pipeline_id=pipeline),
        "policy": [
            "Ebook/social growth remains organic-only; paid-ad automation is parked.",
            "Metricool/Google Analytics integration is deferred until those tools are active again.",
            "Mobile UX treats accessibility-audit output as central-pool evidence, but remediation stays PR-gated.",
            "RAMS must not install or execute local skill bundles; HIVE/R2 owns the shared skill pool.",
        ],
    }
