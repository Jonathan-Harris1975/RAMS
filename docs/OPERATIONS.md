# RAMS production operations

**Status:** Paid Koyeb production service  
**Last reviewed:** 26 July 2026

RAMS runs as a single-worker FastAPI service on the paid Koyeb production instance. Use public `/livez` for process liveness and bearer-protected `/readyz`, `/readiness`, `/ops/warmup` and `/ops/excellence` for operational evidence.

## Normal operating contract

- Koyeb health check path: `/health`.
- One process and one Uvicorn worker.
- One heavyweight pipeline at a time across primary `website`, independent `on-brand`, and the retained legacy compatibility lanes `seo-aeo-geo` / `mobile-ux`.
- Website repository target for `website` and the legacy `seo-aeo-geo` / `mobile-ux` lanes.
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

The production publication contract is:

```env
RMS_PUSH_ENABLED=true
RMS_CREATE_PR=true
RMS_WEBSITE_MAX_ISSUES_PER_RUN=5
```

For the unified website lane, `0` means process every eligible Confirmed council `code_fix` in the final report. Each successful task is committed and pushed only on the run's `rms-qa/*` branch. After all eligible tasks have been attempted, RAMS automatically creates or reuses one non-draft GitHub pull request targeting the configured base branch. Failed or manual-review tasks are reported but are not smuggled into the PR. RAMS does not auto-merge.

## Recovery procedure

1. Preserve audit evidence and report artefacts.
2. Check `/health` and `/livez` first.
3. Check authenticated `/readiness` and `/ops/excellence`.
4. Repair missing R2, GitHub, OpenRouter or repository-bootstrap configuration without printing secrets.
5. Run authenticated `/ops/warmup` to prepare local clients only.
6. Run one safe dry-run pipeline before resuming any live-write work.
7. Resume live writes only after clean release-gate evidence and clean target-repository validation evidence; production then pushes validated QA-branch commits and creates the PR automatically.

## Operator commands

```bash
curl -fsS "$BASE_URL/health"
curl -fsS "$BASE_URL/livez"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/readiness"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/ops/excellence"
```

Dry-run unified website smoke, using an actual retained AIMS report key:

```bash
curl -fsS -X POST \
  -H "Authorization: Bearer $RMS_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: rams-production-smoke-$(date -u +%Y%m%dT%H%M%SZ)" \
  -d '{"dry_run":true,"audit_json_key":"audits/website/2026-07/SESSION_ID/website-audit.json","audit_session_id":"SESSION_ID"}' \
  "$BASE_URL/rebuild/website/run"
```

### Unified website remediation trigger

curl -fsS -X POST \
  -H "Authorization: Bearer $RMS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"audit_json_key":"audits/website/2026-07/SESSION_ID/website-audit.json","audit_session_id":"SESSION_ID"}' \
  "$BASE_URL/rebuild/website/run"

AIMS normally sends this request automatically after final report publication and temporary cleanup. Operators should use it manually only for recovery/replay with the same exact JSON key.

The current JSON contract is `website-audit-report/v2` with remediation contract `rams-website/v1`; RAMS also accepts retained `website-audit-report/v1` reports for backward compatibility. Version 2 is accepted only when `reportStatus` is `complete`, `operational.ramsDispatchPermitted` is true, and its retention policy matches the governed post-acceptance contract. RAMS consumes the council `masterIssueLedger` as the governed work queue. A row can become an autonomous code fix only when it explicitly carries `classification: code_fix`, `confidence: Confirmed`, an approved `fixClass`, exact `affectedPaths`, deterministic remediation, evidence, and non-empty `sourceFindingIds`; everything else fails closed to review/guidance.


## GitHub write token contract

The `RMS_GITHUB_TOKEN` / `GITHUB_TOKEN` used by production must be a fine-grained token scoped only to repositories RAMS may remediate, with at least:

- **Contents: Read and write** for branch push.
- **Pull requests: Read and write** for idempotent PR lookup/creation.
- Repository metadata read access (implicit/default for fine-grained repository tokens).

RAMS sends Git credentials through an ephemeral Git HTTP extra-header and never stores the token in `origin`. GitHub REST authentication is sent only in the HTTPS Authorization header.
