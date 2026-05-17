# Phase 4C Engineering Auto-PR Gate

This RAMS gate maps these skills into safe automation:

- `writing-plans`
- `systematic-debugging`
- `executing-plans`

Rules:
- A task must be a bounded `code_fix` with an AnchorPatch/v1 plan.
- Patches must stay under the configured file/change limits.
- Protected paths, dependency manifests, generated podcast data, workflows and broad infrastructure files are manual-only.
- Post-patch validation must pass before commit/push.
- Any failure sets `manual_review`; no silent production writes.
