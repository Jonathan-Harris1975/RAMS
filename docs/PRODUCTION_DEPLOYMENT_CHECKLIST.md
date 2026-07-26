# RAMS Koyeb production deployment checklist

**Status:** Operator checklist  
**Last reviewed:** 26 July 2026

## Koyeb service

- Runtime image: multi-stage Docker runtime from this repository.
- Health check path: `/health`.
- Public liveness path: `/livez`.
- Instance profile: paid production instance, single process, single worker.

## Required Koyeb secret bindings

```text
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
OPENROUTER_API_KEY
RMS_WEBSITE_REPO_URL
RMS_AIMS_REPO_URL
GITHUB_TOKEN_WEBSITE_AUDITS or RMS_GITHUB_TOKEN (fine-grained: Contents read/write + Pull requests read/write)
RMS_API_KEY
OPS_ALERT_WEBHOOK_URL
OPS_EVENT_INGEST_TOKEN
RMS_RELEASE_ID
```

## Required non-secret environment values

```env
APP_ENV=production
WEB_CONCURRENCY=1
UVICORN_WORKERS=1
R2_ENDPOINT=https://3fb60a7136e950a7ec74959b45e4635e.r2.cloudflarestorage.com
R2_REGION=auto
R2_BUCKET_AUDITS=audits
R2_PUBLIC_BASE_URL_AUDITS=https://pub-f6b6cfd7d07e46f695d08e4a8dc3bd6b.r2.dev
R2_BUCKET_HIVE_SKILLS=hive-skills
R2_PUBLIC_BASE_URL_HIVE_SKILLS=https://pub-da50a6512f164566955a3076a1c795ef.r2.dev
RMS_REPO_BOOTSTRAP_ENABLED=true
RMS_REPO_BASE_DIR=/tmp/rams-repos
RMS_WEBSITE_REPO_BRANCH=main
RMS_AIMS_REPO_BRANCH=main
RMS_WEBSITE_REPO_PATH=/tmp/rams-repos/website
RMS_AIMS_REPO_PATH=/tmp/rams-repos/aims
RMS_MAX_CONCURRENT_PIPELINES=1
RMS_MAX_ISSUES_PER_RUN=1
RMS_WEBSITE_MAX_ISSUES_PER_RUN=0
RMS_SINGLE_WORKER_MODE=true
RMS_OPENROUTER_LOG_PROMPTS=false
RMS_OPENROUTER_DATA_COLLECTION=deny
RMS_VALIDATE_AFTER_EACH_TASK=true
RMS_REVERT_ON_VALIDATION_FAILURE=true
RMS_ALLOW_UNAUTHENTICATED_DEV=false
```

## Production live-write permission

```env
RMS_DRY_RUN=false
RMS_LIVE_WRITE_ENABLED=true
RMS_PUSH_ENABLED=true
RMS_CREATE_PR=true
```

This is the operational website-remediation contract: RAMS writes only to `rms-qa/*`, pushes validated commits, then creates or reuses one non-draft PR per run. The PR is not auto-merged. `RMS_CREATE_PR=true` is rejected at configuration load unless `RMS_PUSH_ENABLED=true` and a usable GitHub write token/repository URLs are present.

## Dry-run or staging mode

```env
RMS_DRY_RUN=true
RMS_LIVE_WRITE_ENABLED=false
RMS_PUSH_ENABLED=false
RMS_CREATE_PR=false
```

## Verification commands

```bash
curl -fsS "$BASE_URL/health"
curl -fsS "$BASE_URL/livez"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/readiness"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/readyz"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/ops/warmup"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/ops/excellence"
```

Expected shapes:

- `/health` and `/livez`: `status=ok` and all four pipeline IDs (`website`, `seo-aeo-geo`, `mobile-ux`, `on-brand`) listed as `idle` or `running`.
- `/readiness` and `/readyz`: `status=ready` when dependencies are available, otherwise `status=degraded` with dependency detail.
- `/ops/warmup`: `status=warm`, `warmupScope` lists local warm-up, `excludedWork` includes OpenRouter requests, R2, repositories, audits and validation.
- `/ops/excellence`: `status=healthy` when the audits bucket verifies, otherwise `status=degraded`; includes `liveWriteControls`, `deploymentContract`, `modelProviderPolicy` and `auditStorage`. Confirm `pushEnabled=true`, `createPr=true`, and `websiteMaxIssuesPerRun=0`.

Safe dry-run smoke:

```bash
curl -fsS -X POST \
  -H "Authorization: Bearer $RMS_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: rams-production-smoke-$(date -u +%Y%m%dT%H%M%SZ)" \
  -d '{"dry_run":true,"audit_json_key":"audits/website/2026-07/SESSION_ID/website-audit.json","audit_session_id":"SESSION_ID"}' \
  "$BASE_URL/rebuild/website/run"
```

The smoke key must point at an actual retained `website-audit.json` from AIMS. RAMS validates `website-audit-report/v1` + `rams-website/v1` before it creates work.
