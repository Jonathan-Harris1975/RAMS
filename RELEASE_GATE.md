> **Document status:** Production reference  
> **Last reviewed:** 16 June 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

# RAMS eco-micro release gate

This gate protects the Koyeb `eco-micro` deployment contract: one process, one Uvicorn worker, one heavyweight RAMS pipeline at a time, bounded memory/disk use, and fail-closed live writes.

## Required local checks

```bash
python -m compileall -q repo_mgmt tests scripts/emicro_benchmark.py
python -m pytest tests/ -q --tb=short
python -m ruff check .
python -m mypy repo_mgmt/ --no-incremental --show-error-codes
python scripts/emicro_benchmark.py --label candidate
```

No paid OpenRouter request is made by these checks.

## Docker checks

A Linux runner with Docker must run:

```bash
docker build --target runtime -t rams-production-check .
docker run --rm rams-production-check python --version
docker run --rm rams-production-check git --version
docker run --rm rams-production-check node --version
docker run --rm rams-production-check npm --version
```

Then boot the image with `.env.example-dry-run` and verify:

- `/health` returns the exact lightweight health contract.
- `/readiness` is authenticated and can be degraded without failing liveness.
- `/ops/warmup` is authenticated, returns `status=warm`, and lists repositories, R2, audits, validation and OpenRouter requests as excluded work.
- A second pipeline cannot run while any pipeline is active.
- Replaying a MAST run key returns the original admission instead of a duplicate run.

`scripts/release_gate.sh` performs these checks when Docker is installed.

## Deployment invariants

- `WEB_CONCURRENCY=1`
- `UVICORN_WORKERS=1`
- `RMS_MAX_CONCURRENT_PIPELINES=1`
- `RMS_MAX_ISSUES_PER_RUN=1`
- `RMS_SINGLE_WORKER_MODE=true`
- `RMS_DRY_RUN=true` for staging
- `RMS_LIVE_WRITE_ENABLED=false` unless a deliberate live-write test is authorised
- `RMS_OPENROUTER_LOG_PROMPTS=false`
- `RMS_OPENROUTER_DATA_COLLECTION=deny`
- `RMS_MIN_FREE_DISK_MB>=256`

## Manual Koyeb verification

After deployment:

```bash
curl -fsS "$BASE_URL/health"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/readiness"
curl -fsS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/ops/warmup"
```

The warm-up endpoint follows the AIMS ops convention but never launches a RAMS audit or model request.
