"""Central HIVE shared skill pool contract for RAMS.

RAMS does not install or execute local Skills.sh bundles. Skill descriptors,
indexes, manifests and audit metadata are centrally controlled by HIVE in the
shared Cloudflare R2 ``hive-skills`` bucket. RAMS consumes the RAMS manifest in
read-only mode and leaves execution/orchestration decisions to HIVE.
"""

from __future__ import annotations

import os
from typing import Any


DEFAULT_HIVE_SKILLS_BASE_URL = "https://pub-da50a6512f164566955a3076a1c795ef.r2.dev"
DEFAULT_HIVE_SKILLS_BUCKET = "hive-skills"
RAMS_MANIFEST_OBJECT_KEY = "manifests/rams-skills-manifest.json"
SHARED_MANIFEST_OBJECT_KEY = "manifests/shared-skill-pool-manifest.json"
SEARCH_DOCUMENTS_OBJECT_KEY = "index/search-documents.json"
CAPABILITY_MAP_OBJECT_KEY = "index/capability-map.json"
REPO_MAP_OBJECT_KEY = "index/repo-map.json"
REFERENCE_PREFIX_MAP_OBJECT_KEY = "index/reference-prefix-map.json"
R2_OBJECT_LAYOUT_OBJECT_KEY = "index/r2-object-layout.json"
SKILL_SYNC_REPORT_OBJECT_KEY = "audits/skill-sync-report.json"
RISK_GATE_REPORT_OBJECT_KEY = "audits/risk-gate-report.json"
REPO_READINESS_REPORT_OBJECT_KEY = "audits/repo-integration-readiness-report.json"


def _env_value(name: str, default: str) -> str:
    """Return a trimmed environment value or a safe default."""
    value = os.getenv(name, default).strip()
    return value or default


def _base_url() -> str:
    return _env_value("R2_PUBLIC_BASE_URL_HIVE_SKILLS", DEFAULT_HIVE_SKILLS_BASE_URL).rstrip("/")


def _bucket() -> str:
    return _env_value("R2_BUCKET_HIVE_SKILLS", DEFAULT_HIVE_SKILLS_BUCKET)


def _url_for(object_key: str) -> str:
    return f"{_base_url()}/{object_key.lstrip('/')}"


def rams_skill_pool_contract(*, pipeline_id: str | None = None) -> dict[str, Any]:
    """Return the read-only central skill-pool contract used by RAMS reports.

    The function intentionally does not fetch remote JSON during normal report
    generation. It records where RAMS should read from once the R2 bucket and AI
    search API are live, while keeping CI, dry-runs and report serialisation
    deterministic and network-free.
    """
    return {
        "source": "HIVE shared skill pool",
        "mode": "central-r2-read-only",
        "pipeline": pipeline_id,
        "bucket": _bucket(),
        "publicBaseUrl": _base_url(),
        "manifest": {
            "repo": "RAMS",
            "objectKey": RAMS_MANIFEST_OBJECT_KEY,
            "url": _url_for(RAMS_MANIFEST_OBJECT_KEY),
        },
        "sharedManifest": {
            "objectKey": SHARED_MANIFEST_OBJECT_KEY,
            "url": _url_for(SHARED_MANIFEST_OBJECT_KEY),
        },
        "indexes": {
            "searchDocuments": {
                "objectKey": SEARCH_DOCUMENTS_OBJECT_KEY,
                "url": _url_for(SEARCH_DOCUMENTS_OBJECT_KEY),
            },
            "capabilityMap": {
                "objectKey": CAPABILITY_MAP_OBJECT_KEY,
                "url": _url_for(CAPABILITY_MAP_OBJECT_KEY),
            },
            "repoMap": {
                "objectKey": REPO_MAP_OBJECT_KEY,
                "url": _url_for(REPO_MAP_OBJECT_KEY),
            },
            "referencePrefixMap": {
                "objectKey": REFERENCE_PREFIX_MAP_OBJECT_KEY,
                "url": _url_for(REFERENCE_PREFIX_MAP_OBJECT_KEY),
            },
            "r2ObjectLayout": {
                "objectKey": R2_OBJECT_LAYOUT_OBJECT_KEY,
                "url": _url_for(R2_OBJECT_LAYOUT_OBJECT_KEY),
            },
        },
        "auditReports": {
            "skillSync": {
                "objectKey": SKILL_SYNC_REPORT_OBJECT_KEY,
                "url": _url_for(SKILL_SYNC_REPORT_OBJECT_KEY),
            },
            "riskGate": {
                "objectKey": RISK_GATE_REPORT_OBJECT_KEY,
                "url": _url_for(RISK_GATE_REPORT_OBJECT_KEY),
            },
            "repoIntegrationReadiness": {
                "objectKey": REPO_READINESS_REPORT_OBJECT_KEY,
                "url": _url_for(REPO_READINESS_REPORT_OBJECT_KEY),
            },
        },
        "governance": {
            "centralController": "HIVE",
            "repo": "RAMS",
            "localAgentsFolderRequired": False,
            "localSkillInstallRequired": False,
            "allowDirectSkillExecution": False,
            "allowRepoWritesFromSkillMetadata": False,
            "allowedRepoUse": [
                "read RAMS-approved manifest metadata",
                "include central skill provenance in reports",
                "use AI search results as planning context once HIVE exposes the API",
            ],
            "blockedRepoUse": [
                "install Skills.sh bundles into the RAMS repo",
                "execute marketplace skills directly from RAMS",
                "treat public R2 metadata as permission to patch, push or deploy",
            ],
        },
    }


def skill_descriptor_url(object_key: str) -> str:
    """Return a public R2 URL for a known skill descriptor object key."""
    return _url_for(object_key)
