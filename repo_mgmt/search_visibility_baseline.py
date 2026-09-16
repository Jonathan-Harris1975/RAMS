"""Native search-visibility baseline metadata for RAMS reports."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from repo_mgmt.config import PipelineId
from repo_mgmt.local_skills import local_skill_reference, rams_local_skills_contract

_SEARCH_VISIBILITY_CAPABILITIES: list[dict[str, Any]] = [
    {
        "id": "RAMS-sk001",
        "name": "audit-evidence-normalisation",
        "source": "RAMS repository-local native capability",
        "sourceUri": local_skill_reference("RAMS-sk001"),
        "implementationPaths": [
            "repo_mgmt/audit_reader.py",
            "repo_mgmt/issue_normaliser.py",
            "repo_mgmt/search_visibility_baseline.py",
        ],
        "coverage": [
            "crawlability and indexation evidence",
            "technical and on-page search signals",
            "extractable answers and entity clarity",
            "AI citation-readiness evidence",
            "llms.txt and structured-context coverage",
        ],
        "purpose": "Normalise grounded SEO, AEO, GEO and LLMO audit evidence without introducing downloaded instructions or invented market data.",
    }
]

_SEARCH_VISIBILITY_BASELINE: dict[str, Any] = {
    "batch": "Batch 1 - Search visibility baseline",
    "lane": "Lane 1 - Autonomous",
    "mode": "reports-only",
    "capabilities": _SEARCH_VISIBILITY_CAPABILITIES,
    # Existing report consumers may still read `skills`; the alias contains
    # the same repository-local capability record and no external descriptor.
    "skills": _SEARCH_VISIBILITY_CAPABILITIES,
    "localSkillCatalogue": rams_local_skills_contract(pipeline_id="seo-aeo-geo"),
    "guardrails": [
        "Reports only; no public page edits.",
        "No commits, pushes, pull requests, deployments, DNS changes, Cloudflare changes or outreach sends.",
        "Every remediation from this baseline must become a separate approval-gated patch before production code or content changes.",
    ],
    "ramsContract": {
        "pipeline": "seo-aeo-geo",
        "targetRepo": "RMS_WEBSITE_REPO_PATH",
        "allowedAutonomousActions": [
            "read approved SEO/AEO/GEO audit artefacts",
            "normalise findings",
            "rank tasks",
            "publish dry-run or live reports",
            "describe the local native capability provenance",
        ],
        "blockedAutonomousActions": [
            "page edits from baseline alone",
            "automatic merge or deployment",
            "DNS or Cloudflare mutation",
            "external skill download or execution",
            "outreach sending",
        ],
    },
}


def search_visibility_baseline_for(pipeline_id: PipelineId) -> dict[str, Any] | None:
    """Return Batch 1 metadata for the SEO/AEO/GEO pipeline only."""

    if pipeline_id != "seo-aeo-geo":
        return None
    return deepcopy(_SEARCH_VISIBILITY_BASELINE)
