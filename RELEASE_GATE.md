> **Document status:** Production release gate  
> **Last reviewed:** 26 July 2026  
> **Operational authority:** README, SECURITY policy and operations guide.

# RAMS production release gate

This gate protects the paid Koyeb production contract: one process, one Uvicorn worker, one heavyweight RAMS pipeline at a time, bounded local resources, authenticated operational endpoints and fail-closed live writes.

## Required local checks

Run these before approving a deployment candidate:

```bash
python -m compileall -q repo_mgmt tests scripts/emicro_benchmark.py
python -m pytest tests/ -q --tb=short
python -m ruff check .
python -m mypy repo_mgmt/ --no-incremental --show-error-codes
python scripts/emicro_benchmark.py --label candidate
```

These checks must not make paid OpenRouter requests.

## Docker checks

A Linux runner with Docker must run:

```bash
docker build --target runtime -t rams-production-check .
docker run --rm rams-production-check python --version
docker run --rm rams-production-check git --version
docker run --rm rams-production-check node --version
docker run --rm rams-production-check npm --version
docker run --rm rams-production-check node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 20 ? 0 : 1)"
```

Then boot the image with `.env.example-dry-run` and verify:

- `/health` and `/livez` return the exact lightweight health contract.
- `/readiness` and `/readyz` require bearer auth and may report `degraded` without failing liveness.
- `/ops/warmup` requires bearer auth, returns `status=warm`, and lists repositories, R2, audits, validation and OpenRouter requests as excluded work.
- `/ops/excellence` requires bearer auth and exposes live-write controls, model privacy policy, release identity and R2 verification state.
- A second pipeline cannot start while any pipeline is active.
- Replaying an idempotency key returns the original admission instead of a duplicate run.

`scripts/release_gate.sh` performs the local checks plus Docker boot probes when Docker is installed.

## Deployment invariants

Production and staging must preserve these unless a new engineering review explicitly changes the contract:

```env
WEB_CONCURRENCY=1
UVICORN_WORKERS=1
RMS_MAX_CONCURRENT_PIPELINES=1
RMS_MAX_ISSUES_PER_RUN=1
RMS_WEBSITE_MAX_ISSUES_PER_RUN=0
RMS_SINGLE_WORKER_MODE=true
RMS_OPENROUTER_LOG_PROMPTS=false
RMS_OPENROUTER_DATA_COLLECTION=deny
RMS_MIN_FREE_DISK_MB=256
RMS_TEMP_CLEANUP_ENABLED=true
RMS_VALIDATE_AFTER_EACH_TASK=true
RMS_REVERT_ON_VALIDATION_FAILURE=true
RMS_ALLOW_UNAUTHENTICATED_DEV=false
```

Dry-run or staging mode:

```env
RMS_DRY_RUN=true
RMS_LIVE_WRITE_ENABLED=false
RMS_PUSH_ENABLED=false
RMS_CREATE_PR=false
```

Paid production live-write permission:

```env
RMS_DRY_RUN=false
RMS_LIVE_WRITE_ENABLED=true
RMS_PUSH_ENABLED=true
RMS_CREATE_PR=true
```

A production candidate is not release-ready unless validated live commits can authenticate to GitHub, push only to `rms-qa/*`, and create/reuse the matching non-draft pull request. PR merge remains a separate human/repository-policy action.

## Manual Koyeb verification

After deployment:

```bash
curl -fsS "$BASE_URL/health"
curl -fsS "$BASE_URL/livez"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/readiness"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/readyz"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/ops/warmup"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/ops/excellence"
```

Safe dry-run trigger with idempotency:

```bash
curl -fsS -X POST \
  -H "Authorization: Bearer $RMS_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: rams-production-smoke-$(date -u +%Y%m%dT%H%M%SZ)" \
  -d '{"dry_run":true}' \
  "$BASE_URL/rebuild/seo-aeo-geo/run"
```

Do not run a live-write request until dry-run evidence, release-gate evidence and target-repository validation evidence are all clean. Once admitted, production is expected to push the validated QA branch and create the PR automatically.
