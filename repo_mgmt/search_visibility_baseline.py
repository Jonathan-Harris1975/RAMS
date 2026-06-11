"""Batch 1 search visibility baseline metadata for RAMS reports.

This module keeps the central HIVE/R2 Lane 1 setup visible in RAMS SEO/AEO/GEO
reports without granting RAMS permission to edit pages from the baseline alone.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from repo_mgmt.config import PipelineId
from repo_mgmt.hive_skill_pool import rams_skill_pool_contract, skill_descriptor_url

_SEARCH_VISIBILITY_BASELINE: dict[str, Any] = {
    "batch": "Batch 1 - Search visibility baseline",
    "lane": "Lane 1 - Autonomous",
    "mode": "reports-only",
    "skills": [
        {
            "name": "seo-audit",
            "source": "HIVE shared skill pool",
            "sourceUrl": "https://skills.sh/coreyhaines31/marketingskills/seo-audit",
            "descriptorUrl": skill_descriptor_url("skills/S088_seo-audit.json"),
            "descriptorObjectKey": "skills/S088_seo-audit.json",
            "purpose": "Traditional SEO baseline for crawlability, indexation, technical foundations, on-page signals, content quality and authority evidence.",
        },
        {
            "name": "ai-seo",
            "source": "HIVE shared skill pool",
            "sourceUrl": "https://skills.sh/coreyhaines31/marketingskills/ai-seo",
            "descriptorUrl": skill_descriptor_url("skills/S164_ai-seo.json"),
            "descriptorObjectKey": "skills/S164_ai-seo.json",
            "purpose": "AEO/GEO/LLMO baseline for extractable answers, entity clarity, AI citation readiness, llms.txt coverage and structured context.",
        },
    ],
    "centralSkillPool": rams_skill_pool_contract(pipeline_id="seo-aeo-geo"),
    "guardrails": [
        "Reports only; no public page edits.",
        "No commits, pushes, pull requests, deployments, DNS changes, Cloudflare changes, or outreach sends.",
        "Every remediation from this baseline must become a separate Lane 2 approval-gated patch before production code or content changes.",
    ],
    "ramsContract": {
        "pipeline": "seo-aeo-geo",
        "targetRepo": "RMS_WEBSITE_REPO_PATH",
        "allowedAutonomousActions": [
            "read RAMS-approved central HIVE skill manifest metadata",
            "read latest SEO/AEO/GEO audit artefacts",
            "normalise findings",
            "rank tasks",
            "publish dry-run/live reports",
        ],
        "blockedAutonomousActions": [
            "page edits from baseline alone",
            "auto-merge",
            "auto-deploy",
            "DNS or Cloudflare mutation",
            "local Skills.sh install or execution",
            "outreach sending",
        ],
    },
}


def search_visibility_baseline_for(pipeline_id: PipelineId) -> dict[str, Any] | None:
    """Return Batch 1 metadata for the SEO/AEO/GEO pipeline only."""
    if pipeline_id != "seo-aeo-geo":
        return None
    return deepcopy(_SEARCH_VISIBILITY_BASELINE)
