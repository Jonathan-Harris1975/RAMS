> **Document status:** Production reference  
> **Last reviewed:** 21 June 2026  
> **Operational authority:** README, SECURITY policy, release gate and operations guide.

# Repository Automation Management Service (RAMS)

RAMS is the controlled Repository Automation Management Service for the website and AIMS estate. It is a Python/FastAPI service deployed to the paid Koyeb production instance, with bounded model use, R2 evidence handling, branch-scoped repository writes and fail-closed safety gates.

## Production responsibilities

- Run the `seo-aeo-geo`, `mobile-ux` and `on-brand` audit pipelines.
- Read audit evidence from the governed Cloudflare R2 `audits` bucket.
- Produce dry-run plans, validated patch artefacts and live reports.
- Keep `seo-aeo-geo` and `mobile-ux` mapped to the website repository.
- Keep `on-brand` mapped to the AIMS / AI-management-suite repository.
- Use the shared HIVE skills bucket for central skill discovery.
- Expose authenticated operational evidence for readiness, warm-up, reports and excellence checks.

## Endpoint contract

| Endpoint | Auth | Purpose |
|---|---:|---|
| `GET /health` | No | Lightweight public process health for Koyeb checks. |
| `GET /livez` | No | Public liveness alias with the same lightweight payload. |
| `GET /readiness` | Bearer | Dependency readiness, repository materialisation and admission state. |
| `GET /readyz` | Bearer | Readiness alias for operators and deployment probes. |
| `GET /ops/warmup` | Bearer | Local configuration and HTTP client warm-up only. No audits, model calls, R2 checks, validation or repository mutation. |
| `GET /ops/excellence` | Bearer | Production evidence for R2 verification, release identity, live-write controls, model policy and Koyeb contract. |
| `GET /reports/*` | Bearer | Local dry-run report metadata or live R2 report reads with bounded size checks. |
| `POST /rebuild/{pipeline_id}/run` | Bearer | Admit one heavyweight pipeline run, with idempotency replay and global single-run enforcement. |

## Production mode, without the fog machine

The current production environment is allowed to run governed live-write workflows only when both gates are explicitly set and parseable:

```env
RMS_DRY_RUN=false
RMS_LIVE_WRITE_ENABLED=true
```

That does **not** mean uncontrolled publishing. With the current production contract:

```env
RMS_PUSH_ENABLED=false
RMS_CREATE_PR=false
```

RAMS may run production workflows, create validated local commits/artefacts on a RAMS QA branch and publish the run report to R2. It must not push branches or create pull requests until those controls are deliberately enabled by an operator after clean dry-run, release-gate and target-repository validation evidence.

## Deployment invariants

```env
WEB_CONCURRENCY=1
UVICORN_WORKERS=1
RMS_MAX_CONCURRENT_PIPELINES=1
RMS_MAX_ISSUES_PER_RUN=1
RMS_SINGLE_WORKER_MODE=true
RMS_OPENROUTER_LOG_PROMPTS=false
RMS_OPENROUTER_DATA_COLLECTION=deny
RMS_VALIDATE_AFTER_EACH_TASK=true
RMS_REVERT_ON_VALIDATION_FAILURE=true
RMS_ALLOW_UNAUTHENTICATED_DEV=false
RMS_TEMP_CLEANUP_ENABLED=true
RMS_MIN_FREE_DISK_MB=256
```

## Local verification

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python -m compileall -q repo_mgmt tests scripts/emicro_benchmark.py
python -m pytest tests/ -q --tb=short
python -m ruff check .
python -m mypy repo_mgmt/ --no-incremental --show-error-codes
python scripts/emicro_benchmark.py --label candidate
```

`scripts/release_gate.sh` adds Docker runtime checks when Docker is available.

## Production references

- [`RAMS-KOYEB-PRODUCTION-ENV.txt`](RAMS-KOYEB-PRODUCTION-ENV.txt) contains the paid Koyeb production environment contract.
- [`.env.example-dry-run`](.env.example-dry-run) is deliberately non-production.
- [`RELEASE_GATE.md`](RELEASE_GATE.md) defines the local, Docker and Koyeb verification gauntlet.
- [`SECURITY.md`](SECURITY.md) defines authentication, secret and repository-mutation boundaries.
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) defines operator recovery and dry-run-first procedures.
- [`docs/OPERATIONAL_ALERTING.md`](docs/OPERATIONAL_ALERTING.md) defines HIVE Ops alerting and deployment-watch expectations.
- [`docs/OPTIMISATION_ENGINE.md`](docs/OPTIMISATION_ENGINE.md) defines the deterministic, confidence-scored, reversible Optimisation Subsystem for AIMS.
