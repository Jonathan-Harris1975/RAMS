# Skills.sh Batch 1 — Search visibility baseline

Batch 1 configures the report-only Lane 1 search visibility baseline for the Jonathan Harris ecosystem.

## Skills

| Skill | Source | Purpose |
|---|---|---|
| `seo-audit` | `coreyhaines31/marketingskills` | SEO audit framework for crawlability, indexation, technical foundations, on-page quality, content quality, and authority evidence. |
| `ai-seo` | `coreyhaines31/marketingskills` | AEO/GEO/LLMO framework for extractable answers, entity clarity, AI citation readiness, llms.txt coverage, and AI-search visibility. |

## Install command

Run from the repository root when network access to GitHub is available:

```bash
scripts/setup-batch-1-skills.sh
```

The script runs:

```bash
DISABLE_TELEMETRY=1 npx --yes skills@latest add coreyhaines31/marketingskills --skill seo-audit ai-seo -y
```

## Local governance files

These files are intentionally committed so the agent has ecosystem context before the third-party skills are installed:

- `.agents/product-marketing.md`
- `.agents/product-marketing-context.md`
- `.agents/skills/batch-1-search-visibility/SKILL.md`

## Operating guardrail

Batch 1 is autonomous for scanning, validation, source collection, and report generation only. It must not edit public pages, merge pull requests, deploy, alter DNS/Cloudflare configuration, or send outreach. Any remediation becomes a separate Lane 2 approval-gated patch.
