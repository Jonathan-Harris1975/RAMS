# Lane 1 autonomous skills setup

This repository has been prepared for the full Lane 1 skills set from the Skills.sh ecosystem plan.

## Included Lane 1 skills

| Batch | Skill | Repository | Role |
|---|---|---|---|
| Batch 1 | seo-audit | coreyhaines31/marketingskills | SEO audit and evidence reporting |
| Batch 1 | ai-seo | coreyhaines31/marketingskills | AEO/GEO and AI-search visibility checks |
| Batch 2 | agent-browser | vercel-labs/agent-browser | Rendered browser automation and screenshot evidence |
| Batch 2 | playwright-best-practices | currents-dev/playwright-best-practices-skill | Playwright reliability and mobile UX audit hard gates |
| Batch 2 | webapp-testing | anthropics/skills | Route, report and web regression testing guidance |
| Batch 3 | firecrawl-crawl | firecrawl/cli | Crawl coverage and route discovery |
| Batch 3 | firecrawl-scrape | firecrawl/cli | RSS/source article extraction support |
| Batch 3 | firecrawl-search | firecrawl/cli | Research/source discovery reporting |
| Batch 4 | verification-before-completion | obra/superpowers | Evidence-before-done gate |
| Batch 4 | xlsx | anthropics/skills | Spreadsheet/report workbook handling |
| Batch 4 | pdf | anthropics/skills | PDF/report handling |
| Later Lane 1 | web-perf | cloudflare/skills | Cloudflare/site performance checks |
| Later Lane 1 | sentry-cli | sentry/dev | Monitoring/release/error tracking support |
| Later Lane 1 | browser-use | browser-use/browser-use | Backup visual browser automation |

## Install command

Run from the repository root:

```bash
scripts/setup-lane-1-skills.sh
```

The script installs the external Skills.sh skills. Repo-side governance, metadata and audit/report integration files are already included in this patch.

## Governance

Lane 1 may autonomously crawl, scan, extract, validate, screenshot, score, monitor and generate reports. It must not auto-publish, auto-merge, auto-deploy, send outreach, or alter DNS/Cloudflare routing.
