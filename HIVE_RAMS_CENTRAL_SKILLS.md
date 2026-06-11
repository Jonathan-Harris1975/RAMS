# RAMS HIVE Shared Skill Pool Integration

RAMS now treats HIVE as the central controller for shared skills.

## R2 details

```text
R2_PUBLIC_BASE_URL_HIVE_SKILLS=https://pub-da50a6512f164566955a3076a1c795ef.r2.dev
R2_BUCKET_HIVE_SKILLS=hive-skills
```

## Objects RAMS expects

| Object | Purpose |
|---|---|
| `manifests/rams-skills-manifest.json` | RAMS-approved skill list and repo permissions |
| `manifests/shared-skill-pool-manifest.json` | Shared pool summary |
| `index/search-documents.json` | AI-search-ready skill documents |
| `index/capability-map.json` | Capability-to-skill lookup |
| `index/repo-map.json` | Repo-to-skill mapping |
| `index/reference-prefix-map.json` | Reference prefix and descriptor lookup |
| `audits/skill-sync-report.json` | Shared pool sync status |
| `audits/risk-gate-report.json` | Review gate/risk posture |
| `audits/repo-integration-readiness-report.json` | Repo integration readiness |

## Contract

RAMS is a read-only consumer. It can include central skill provenance in reports and use HIVE search results as planning context once exposed, but it must not install local Skills.sh bundles or execute marketplace skills directly.

## Files no longer needed

- `.agents/`
- `scripts/setup-batch-1-skills.sh`
- `scripts/setup-lane-1-skills.sh`
