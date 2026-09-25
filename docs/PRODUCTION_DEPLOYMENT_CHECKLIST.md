# RAMS Koyeb production deployment checklist

**Status:** Operator checklist  
**Last reviewed:** 22 September 2026

## Koyeb service

- Runtime image: multi-stage Docker runtime from this repository.
- Health check path: `/health`.
- Public liveness path: `/livez`.
- Instance profile: exactly one paid production instance, one process and one worker.

## Required Koyeb secret/sensitive bindings

Koyeb service configuration must contain only these bindings:

```env
OPENROUTER_API_KEY={{ secret.OPENROUTER_API_KEY }}
R2_ACCESS_KEY_ID={{ secret.R2_ACCESS_KEY_ID }}
R2_SECRET_ACCESS_KEY={{ secret.R2_SECRET_ACCESS_KEY }}
RMS_API_KEY={{ secret.RMS_API_KEY }}
RMS_GITHUB_TOKEN={{ secret.GITHUB_TOKEN_WEBSITE_AUDITS }}
RMS_WEBSITE_REPO_URL={{ secret.RMS_WEBSITE_REPO_URL }}
RMS_AIMS_REPO_URL={{ secret.RMS_AIMS_REPO_URL }}
```

`AIMS_API_KEY` is not a RAMS runtime dependency and must not be added to this service. Optional HIVE alert/release secrets are also not part of the current RAMS Koyeb contract.

## Version-controlled production configuration

All non-secret production values are baked into `Dockerfile`. Application-safe fallback defaults remain in `repo_mgmt/config.py`. Do not duplicate those values in Koyeb service environment settings. This keeps deployment policy reviewable in Git and prevents platform-side configuration drift.

The current production write gates are:

```env
RMS_DRY_RUN=false
RMS_LIVE_WRITE_ENABLED=true
RMS_PUSH_ENABLED=true
RMS_CREATE_PR=true
RMS_SINGLE_WORKER_MODE=true
RMS_DEPLOYMENT_INSTANCE_COUNT=1
WEB_CONCURRENCY=1
UVICORN_WORKERS=1
```

RAMS may mutate and validate its ephemeral checkout, but it does not push branches or create GitHub pull requests in this production profile. The GitHub token is retained for authenticated cloning/refresh of private target repositories.

## Verification commands

Before runtime probes, confirm the automatic GitHub production watcher has `KOYEB_TOKEN` and `KOYEB_SERVICE`. Missing configuration fails the watcher. A release is production-attested only after the bounded Koyeb watch observes the expected source SHA, the exact-SHA JSON evidence is retained, and the governed ecosystem-smoke dispatch succeeds.

The deterministic source contract can be checked without provider credentials:

```bash
python -m pytest tests/test_deployment_watch_workflow.py -q
```

```bash
curl -fsS "$BASE_URL/health"
curl -fsS "$BASE_URL/livez"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/readiness"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/readyz"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/ops/warmup"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/ops/excellence"
```

Expected shapes:

- `/health` and `/livez`: `status=ok` and all five pipeline IDs (`website`, `content`, `seo-aeo-geo`, `mobile-ux`, `on-brand`) listed as `idle` or `running`.
- `/readiness` and `/readyz`: `status=ready` when dependencies are available, otherwise `status=degraded` with dependency detail. Confirm `single_worker_mode=true`, `single_instance_mode=true`, `process_local_idempotency_safe=true` and `idempotency.scope=process-local`. R2 readiness is a live `HeadBucket` check; storage failure must not make `/health` fail.
- `/ops/warmup`: `status=warm`, `warmupScope` lists local warm-up, `excludedWork` includes OpenRouter requests, R2, repositories, audits and validation.
- `/ops/excellence`: `status=healthy` when the audits bucket verifies, otherwise `status=degraded`; includes `liveWriteControls`, `deploymentContract`, `modelProviderPolicy` and `auditStorage`. Confirm `pushEnabled=true`, `createPr=true`, `maxIssuesPerRun=1`, `configuredInstances=1`, `idempotencyScope=process-local` and `horizontalScalingSupported=false`.

Safe dry-run smoke:

```bash
curl -fsS -X POST \
  -H "Authorization: Bearer $RMS_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: rams-production-smoke-$(date -u +%Y%m%dT%H%M%SZ)" \
  -d '{"dry_run":true,"audit_json_key":"audits/website/2026-07/SESSION_ID/website-audit.json","audit_session_id":"SESSION_ID"}' \
  "$BASE_URL/rebuild/website/run"
```

The smoke key must point at an actual retained `website-audit.json` from AIMS. RAMS validates the current complete `website-audit-report/v2` + `rams-website/v1` contract before it creates work, while retaining explicit v1 compatibility for older final reports.
