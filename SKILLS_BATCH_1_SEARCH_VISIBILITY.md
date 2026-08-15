> **Document status:** Production reference  
> **Last reviewed:** 16 June 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

# RAMS Search Visibility Skills — Central HIVE Pool

RAMS no longer installs Batch 1 search visibility skills into the local repo. The `seo-audit` and `ai-seo` descriptors are centrally controlled by HIVE in the shared Cloudflare R2 skill pool.

## Central source of truth

| Item | Value |
|---|---|
| R2 bucket | `hive-skills` |
| Public base URL | None; bucket is private |
| RAMS manifest | `manifests/rams-skills-manifest.json` |
| Search index | `index/search-documents.json` |
| Descriptor lookup | `index/reference-prefix-map.json` |

## Search visibility descriptors

| Skill | Central descriptor | Purpose |
|---|---|---|
| `seo-audit` | `skills/S088_seo-audit.json` | SEO audit framework for crawlability, indexation, technical foundations, on-page quality, content quality, and authority evidence. |
| `ai-seo` | `skills/S164_ai-seo.json` | AEO/GEO/LLMO framework for extractable answers, entity clarity, AI citation readiness, llms.txt coverage, and AI-search visibility. |

## Operating guardrail

RAMS may use the central manifest as **read-only report/planning context**. It must not install marketplace skills, execute skills directly, edit public pages from skill metadata alone, merge pull requests, deploy, alter DNS/Cloudflare configuration, or send outreach. Any remediation remains a separate approval-gated RAMS patch.
