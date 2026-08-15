> **Document status:** Production reference  
> **Last reviewed:** 16 June 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

# RAMS Lane 1 Skills — Central HIVE Pool

Lane 1 skill metadata is now centrally controlled by HIVE in the shared R2 `hive-skills` bucket. RAMS consumes the RAMS manifest in read-only mode instead of carrying a local `.agents` skill library.

## RAMS skill access contract

| Area | Setting |
|---|---|
| Controller | HIVE |
| RAMS access mode | Central R2 read-only manifest |
| R2 bucket | `hive-skills` |
| Public base URL | None; bucket is private |
| RAMS manifest | `manifests/rams-skills-manifest.json` |
| Search documents | `index/search-documents.json` |
| Capability map | `index/capability-map.json` |
| Repo map | `index/repo-map.json` |

## Local repo rule

RAMS should not contain or install local Skills.sh bundles. The old local `.agents` folder and setup scripts are deprecated and should be removed once this patch is applied.

## Governance

RAMS may autonomously crawl, scan, extract, validate, screenshot, score, monitor and generate reports where the pipeline already allows that behaviour. Central skill metadata does **not** grant permission to auto-publish, auto-merge, auto-deploy, send outreach, alter DNS/Cloudflare routing, or bypass RAMS fail-closed gates.
