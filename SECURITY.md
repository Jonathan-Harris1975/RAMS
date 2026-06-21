# RAMS security policy

**Status:** Production-controlled  
**Last reviewed:** 21 June 2026

RAMS can inspect and alter repositories, so access is deliberately narrow. `/health` and `/livez` are public and lightweight. `/readiness`, `/readyz`, `/ops/warmup`, `/ops/excellence`, report reads and rebuild endpoints require `RMS_API_KEY` unless a local-only developer has explicitly set `RMS_ALLOW_UNAUTHENTICATED_DEV=true`.

## Secret handling

R2, GitHub, OpenRouter, RMS API and HIVE Ops alert credentials belong in Koyeb Secrets. Runtime code treats unresolved `{{ secret.NAME }}` placeholders as missing for required secret-bearing settings. Secret values must not be committed, printed, returned in API responses or echoed in logs.

OpenRouter prompt logging must stay disabled in production:

```env
RMS_OPENROUTER_LOG_PROMPTS=false
RMS_OPENROUTER_DATA_COLLECTION=deny
```

Usage and cost metrics may be logged, but prompts and repository secrets must stay hidden.

## Repository mutation safety

Live writes require all of the following:

- Bearer authentication.
- `RMS_DRY_RUN=false` explicitly present and parseable.
- `RMS_LIVE_WRITE_ENABLED=true` explicitly present and parseable.
- One configured worker and one concurrent pipeline.
- Clean target Git worktree.
- RAMS QA branch prefix, never `main` or `master`.
- Protected-path checks before mutation.
- Validation after each task.
- Task-scoped rollback on validation failure.

Current production keeps these controls deliberately governed:

```env
RMS_PUSH_ENABLED=false
RMS_CREATE_PR=false
```

That preserves production execution and report generation without automatic branch push or pull request creation.

## Operational hardening

Responses include restrictive API security headers. Warm-up never launches audits, validation, R2 checks, model calls or repository mutation. R2 verification and HIVE Ops alerts are bounded and redacted. Report reads use constrained keys and size limits.
