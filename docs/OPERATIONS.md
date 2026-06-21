# RAMS production operations

**Status:** Paid Koyeb production service  
**Last reviewed:** 21 June 2026

RAMS runs as a single-worker FastAPI service on the paid Koyeb production instance. Use public `/livez` for process liveness and bearer-protected `/readyz`, `/readiness`, `/ops/warmup` and `/ops/excellence` for operational evidence.

## Normal operating contract

- Koyeb health check path: `/health`.
- One process and one Uvicorn worker.
- One heavyweight pipeline at a time across `seo-aeo-geo`, `mobile-ux` and `on-brand`.
- Website repository target for `seo-aeo-geo` and `mobile-ux`.
- AIMS repository target for `on-brand`.
- Repository checkouts materialised on demand beneath `/tmp/rams-repos`.
- Reports and live evidence published to the governed `audits` bucket.
- HIVE skills loaded from the shared `hive-skills` bucket when required.

## Production gate meaning

Production live-write permission is controlled by:

```env
RMS_DRY_RUN=false
RMS_LIVE_WRITE_ENABLED=true
```

The production contract currently keeps:

```env
RMS_PUSH_ENABLED=false
RMS_CREATE_PR=false
```

So production mode means RAMS can run governed workflows, validate patches and produce report artefacts. It does not push branches or create pull requests until those separate controls are deliberately changed.

## Recovery procedure

1. Preserve audit evidence and report artefacts.
2. Check `/health` and `/livez` first.
3. Check authenticated `/readiness` and `/ops/excellence`.
4. Repair missing R2, GitHub, OpenRouter or repository-bootstrap configuration without printing secrets.
5. Run authenticated `/ops/warmup` to prepare local clients only.
6. Run one safe dry-run pipeline before resuming any live-write work.
7. Only consider live writes after clean release-gate evidence and clean target-repository validation evidence.

## Operator commands

```bash
curl -fsS "$BASE_URL/health"
curl -fsS "$BASE_URL/livez"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/readiness"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/ops/excellence"
```

Dry-run production smoke:

```bash
curl -fsS -X POST \
  -H "Authorization: Bearer $RMS_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: rams-production-smoke-$(date -u +%Y%m%dT%H%M%SZ)" \
  -d '{"dry_run":true}' \
  "$BASE_URL/rebuild/seo-aeo-geo/run"
```
