---
name: batch-1-search-visibility
description: Use this when running or reviewing the Lane 1 Search visibility baseline for the Jonathan Harris ecosystem. It coordinates seo-audit and ai-seo style checks while keeping the workflow report-only unless a human explicitly approves follow-up changes.
---

# Batch 1 Search Visibility Baseline

## Purpose
Run the first Lane 1 visibility layer for the Jonathan Harris ecosystem: traditional SEO health plus answer-engine and generative-engine visibility. This is a reporting skill, not a page-editing or publishing skill.

## Use alongside
- `seo-audit` for crawlability, indexation, technical foundations, on-page signals, content quality, and authority checks.
- `ai-seo` for AEO/GEO/LLMO checks covering extractable answers, entity clarity, source/citation readiness, llms.txt support, and AI-search visibility.

## Default inputs
Read this context before asking for more information:
1. `.agents/product-marketing.md`
2. `.agents/product-marketing-context.md`
3. repo README / audit README
4. existing SEO/AEO/GEO reports, coverage ledgers, sitemap, robots.txt, llms.txt, and llm-index.json

## Hard guardrails
- Generate reports only.
- Do not edit public pages, templates, blog copy, podcast pages, metadata, schema, sitemap, robots.txt, llms.txt, redirects, DNS, Cloudflare settings, workflows, or deployment config unless the user explicitly asks for an approval-gated Lane 2 patch.
- Do not create commits, pushes, PRs, deployments, or public content from this skill alone.
- Treat R2-hosted podcast episode pages as external audit artefacts unless the task explicitly provides a repo-owned patch target.

## Required output shape
Every report should include:
- audited scope and timestamp
- source URLs/files inspected
- SEO findings grouped by severity
- AEO/GEO findings grouped by page family
- exact affected URL, file, route family, or artefact where available
- confidence level and evidence
- recommended owner
- verification method
- clear marker that Batch 1 is report-only

## Install the companion Skills.sh package
When network access is available, run from the repository root:

```bash
DISABLE_TELEMETRY=1 npx --yes skills@latest add coreyhaines31/marketingskills --skill seo-audit ai-seo -y
```

The Skills.sh CLI installs skills into `.agents/skills/` and creates compatibility links for supported agents. Keep this local coordinating skill in place even after installing the third-party skills because it contains the Jonathan Harris governance rules.
