"""Phase 5 skill metadata for organic growth and accessibility governance."""

from __future__ import annotations

from typing import Any


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
        "mode": "organic-only automation with fail-closed gates",
        "activeSkills": active,
        "parkedSkills": PARKED_SKILLS,
        "policy": [
            "Ebook/social growth remains organic-only; paid-ad automation is parked.",
            "Metricool/Google Analytics integration is deferred until those tools are active again.",
            "Mobile UX now treats accessibility-audit output as rendered evidence, but remediation stays PR-gated.",
        ],
    }
