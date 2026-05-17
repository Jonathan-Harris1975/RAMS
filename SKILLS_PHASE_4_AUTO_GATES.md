# Skills.sh Phase 4 Autonomous Gates

Phase 4 applies the remaining safe Lane 2 skills using automated fail-closed gates.

## 4A: `schema-markup`

Structured data can be applied automatically only when it is template-bounded and validates before release. Invalid JSON-LD is a blocker. Podcast episode data is not collected in the website repo; the podcast page is embed-led and the R2 podcast estate remains authoritative.

## 4B: `social-content`

Social/blog content can auto-publish only when source-backed and brand-safe. The gate checks source integrity, British English, no-hype wording, social contract shape, valid schema and publication metadata. Failures are written to `phase-4-quarantine/` and do not publish.

## 4C: `writing-plans`, `systematic-debugging`, `executing-plans`

Engineering automation can prepare and commit bounded PR-style fixes only after plan, patch scope and validation gates pass. Protected paths, workflows, generated podcast data, dependency manifests and broad infrastructure changes remain manual-only.

## Operating rule

Auto-review, auto-publish, fail-closed. Passing items can proceed without manual review; failed items quarantine or move to `manual_review`.
