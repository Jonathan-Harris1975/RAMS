> **Document status:** Production reference  
> **Last reviewed:** 26 July 2026  
> **Operational authority:** README, SECURITY policy, release gate and operations guide.

# Repository Automation Management Service (RAMS)

RAMS is the controlled Repository Automation Management Service for the website and AIMS estate. It is a Python/FastAPI service deployed to the paid Koyeb production instance, with bounded model use, R2 evidence handling, branch-scoped repository writes and fail-closed safety gates.

## Production responsibilities

- Run the primary unified `website` remediation pipeline from AIMS's final `website-audit.json`, plus the independent `on-brand` lane. Legacy `seo-aeo-geo` and `mobile-ux` endpoints remain for compatibility only.
- Read audit evidence from the governed Cloudflare R2 `audits` bucket.
- Produce dry-run plans, validated patch artefacts and live reports.
- For live runs, push every validated RAMS QA-branch commit and automatically create or reuse one non-draft GitHub pull request for the run.
- Keep `website`, legacy `seo-aeo-geo`, and legacy `mobile-ux` mapped to the website repository.
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

The production publication controls are also enabled:

```env
RMS_PUSH_ENABLED=true
RMS_CREATE_PR=true
```

RAMS still never writes to `main` or `master`. Each eligible fix must pass repository/path safety, the Phase 4C autonomous engineering gate and configured validation. Successful commits are pushed to the run's `rms-qa/*` branch, then RAMS creates or reuses one non-draft GitHub pull request targeting the configured base branch. RAMS does **not** auto-merge the PR.

## Deployment invariants

```env
WEB_CONCURRENCY=1
UVICORN_WORKERS=1
RMS_MAX_CONCURRENT_PIPELINES=1
RMS_MAX_ISSUES_PER_RUN=1
RMS_WEBSITE_MAX_ISSUES_PER_RUN=0  # all eligible Confirmed website code fixes
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

## Unified website audit handoff

AIMS owns the website audit sequence and retains exactly three final audit representations: `website-audit.pdf`, `website-audit.html`, and `website-audit.json`. After AIMS verifies temporary evidence cleanup, it calls `POST /rebuild/website/run` with the exact JSON R2 key. RAMS validates the key shape and report schema before creating any remediation task; it does not discover the website audit through a `latest.json` pointer.

Only council findings with **Confirmed** confidence, source-finding traceability, an exact existing website-repository file path, a bounded approved fix class, and executable remediation can become autonomous `code_fix` work. URLs, routes, missing paths and AIMS/R2-owned dynamic content fail closed to manual review.
The machine contract is `website-audit-report/v1` + `rams-website/v1`; RAMS treats the final council `masterIssueLedger` as the primary remediation queue and only falls back to narrative council rows when that ledger is absent. For the unified `website` lane, `RMS_WEBSITE_MAX_ISSUES_PER_RUN=0` means every eligible Confirmed `code_fix` in that governed ledger is processed in the single AIMS-triggered run rather than silently dropping lower-ranked fixes.
