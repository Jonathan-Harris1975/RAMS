# RAMS production operations

**Status:** Paid Koyeb production service  
**Last reviewed:** 21 September 2026

RAMS runs as a single-worker, single-instance FastAPI service on the paid Koyeb production instance. Use public `/livez` for process liveness and bearer-protected `/readyz`, `/readiness`, `/ops/warmup` and `/ops/excellence` for operational evidence.

## Normal operating contract

- Koyeb health check path: `/health`.
- Exactly one deployment instance, one process and one Uvicorn worker.
- One heavyweight pipeline at a time across primary `website`, independent `on-brand`, and the retained legacy compatibility lanes `seo-aeo-geo` / `mobile-ux`.
- Website repository target for `website` and the legacy `seo-aeo-geo` / `mobile-ux` lanes.
- AIMS repository target for `on-brand`.
- Repository checkouts materialised on demand beneath `/tmp/rams-repos`.
- Reports and live evidence are read/written in the governed `audits` bucket through authenticated R2/S3 access; RAMS does not require `R2_PUBLIC_BASE_URL_AUDITS`.
- RAMS capability metadata is bundled under `config/skills`; no shared skills bucket or external descriptor service is required.

## Dependency integrity

Runtime, bootstrap, build and development dependencies have separate source files and pip-tools-generated SHA-256 locks. Docker, CI and the canonical `scripts/install_production.sh` path use `pip install --require-hashes`; the local RAMS package is installed offline without dependency resolution or build isolation. Run `python scripts/verify_dependency_lock.py --compile` and `python scripts/verify_hash_enforcement.py` before release.

## Scale and idempotency contract

The admission lock and idempotency replay cache are process-local. `RMS_SINGLE_WORKER_MODE=true`, worker count `1` and `RMS_DEPLOYMENT_INSTANCE_COUNT=1` are therefore correctness requirements, not tuning hints. `/readiness` exposes `single_worker_mode`, `single_instance_mode`, `process_local_idempotency_safe` and idempotency metadata. `/ops/excellence` declares `horizontalScalingSupported=false`. RAMS rejects dry-run and live-run admission with HTTP 409 when the contract is unsafe. Add a shared durable admission/idempotency store before any horizontal scale-out.

## R2 readiness, reads and recovery

R2 uses the S3-compatible endpoint and governed `audits` bucket configured by `R2_ENDPOINT`, `R2_REGION` and `R2_BUCKET_AUDITS`; access-key and secret-key values must come from Koyeb Secrets. Never paste live credential values into operator commands, tickets or logs.

`R2Client` construction does not prove storage availability. Authenticated `/readiness`, `/readyz` and `/ops/excellence` use a live `HeadBucket` probe. Endpoint, network, credentials, permission and missing-bucket failures degrade readiness while `/health` and `/livez` remain available for process diagnosis. The client uses short timeouts and botocore standard retries with one retry after the initial attempt.

Storage SDK/transport failures are converted to `R2Error`. Diagnostics retain the operation, bucket/key context and safe error code/status but do not echo raw provider messages. Report/evidence paths use bounded reads where configured and reject objects over their byte ceilings. Operators should treat an R2 failure as a storage/configuration incident, repair the endpoint/bucket/credential binding, re-check authenticated readiness, then run a dry-run before resuming live-write work.

## Production gate meaning

Production live-write permission is controlled by:

```env
RMS_DRY_RUN=false
RMS_LIVE_WRITE_ENABLED=true
```

The current production publication contract is:

```env
RMS_PUSH_ENABLED=true
RMS_CREATE_PR=true
RMS_MAX_ISSUES_PER_RUN=1
```

Each run remains bounded to one issue. RAMS can make and validate governed changes in the ephemeral checkout, but this profile does not push `rms-qa/*` branches or create GitHub pull requests.

## Recovery procedure

1. Preserve audit evidence and report artefacts.
2. Check `/health` and `/livez` first.
3. Check authenticated `/readiness` and `/ops/excellence`.
4. Repair missing R2, GitHub, OpenRouter or repository-bootstrap configuration without printing secrets.
5. Run authenticated `/ops/warmup` to prepare local clients only.
6. Run one safe dry-run pipeline before resuming any live-write work.
7. Exercise `python scripts/disposable_live_branch_check.py` (or the equivalent isolated staging branch recovery) and retain the result.
8. Resume live writes only after clean release-gate evidence and clean target-repository validation evidence; the current production profile enables governed GitHub push and non-draft PR creation.

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

## Model-governance operation

HIVE submits its evaluated OpenRouter registry to the authenticated
`POST /ops/model-governance/apply` endpoint. Treat HTTP 200 with `persisted: true` as success, HTTP
422 as an invalid selection or premium justification, and HTTP 503 as a persistence failure. After
an applied change, run one dry-run pipeline and confirm the report's per-model request, token, cost,
duration and premium-request totals. Do not manually enable the premium chair: it is enabled only by
an active, independently approved HIVE justification restored from R2.

The complete selection, retirement, audit and approval procedure is in
`docs/MODEL_GOVERNANCE.md`. Cost targets prompt review and optimisation; they do not stop authorised
RAMS workloads.


## GitHub write token contract

The `RMS_GITHUB_TOKEN` / `GITHUB_TOKEN` used by production must be a fine-grained token scoped only to repositories RAMS may remediate, with at least:

- **Contents: Read** for authenticated clone/fetch of the private target repositories.
- Repository metadata read access (implicit/default for fine-grained repository tokens).

With `RMS_PUSH_ENABLED=true` and `RMS_CREATE_PR=true`, the GitHub token requires repository **Contents: Read and write** and **Pull requests: Read and write** permissions for the approved target repositories.

RAMS sends Git credentials through an ephemeral Git HTTP extra-header and never stores the token in `origin`. GitHub REST authentication is sent only in the HTTPS Authorization header.
