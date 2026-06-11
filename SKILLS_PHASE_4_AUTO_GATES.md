# RAMS Phase 4 Autonomous Gates — Central HIVE Pool

Phase 4 remains governed by automated fail-closed gates, but the skill metadata is now sourced from the central HIVE/R2 shared skill pool rather than local repo skill files.

## 4A: `schema-markup`

Structured data can be applied automatically only when it is template-bounded and validates before release. Invalid JSON-LD is a blocker. Podcast episode data is not collected in the website repo; the podcast page is embed-led and the R2 podcast estate remains authoritative.

## 4B: `social-content`

Social/blog content can auto-publish only when source-backed and brand-safe. The gate checks source integrity, British English, no-hype wording, social contract shape, valid schema and publication metadata. Failures are written to quarantine/manual-review outputs and do not publish.

## 4C: `writing-plans`, `systematic-debugging`, `executing-plans`

Engineering automation can prepare and commit bounded PR-style fixes only after plan, patch scope and validation gates pass. Protected paths, workflows, generated podcast data, dependency manifests and broad infrastructure changes remain manual-only.

## Operating rule

Central skill metadata is planning context, not permission. Passing items can proceed only through the existing RAMS gates; failed items quarantine or move to manual review.
