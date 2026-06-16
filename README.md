> **Document status:** Production reference  
> **Last reviewed:** 16 June 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

# Repository Automation Management Service (RAMS)

RAMS audits and proposes controlled changes for the website and AIMS repositories. It is a Python/FastAPI service deployed on Koyeb with dry-run-first execution, repository safety boundaries and validation gates.

## Production responsibilities

- SEO/AEO/GEO, mobile UX and on-brand audit pipelines.
- Evidence ingestion from the dedicated R2 audits bucket.
- Review-gated patch planning and validation.
- Isolated Git branch/commit workflows.
- Shared HIVE skill-pool discovery.
- Bounded operator endpoints for warm-up, readiness and report access.

## Health contract

| Endpoint | Auth | Purpose |
|---|---:|---|
| `GET /health` | No | Stable public service health |
| `GET /livez` | No | Koyeb liveness alias |
| `GET /readiness` | Bearer | Dependency readiness |
| `GET /readyz` | Bearer | Koyeb/operator readiness alias |
| `GET /ops/warmup` | Bearer | Warm local configuration and clients |

## Local verification

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python -m pytest tests -q
python -m ruff check .
python -m mypy repo_mgmt --no-incremental --show-error-codes
```

## Deployment

Use the multi-stage Docker image. The runtime is non-root and includes Python, Git, Node.js and npm because target repositories require mixed validation commands. Keep a single RAMS worker and one concurrent pipeline on the current Koyeb instance unless profiling supports a change.

Production writes remain disabled until the dry-run evidence, release gate and explicit live-write settings are all satisfied. See [`RELEASE_GATE.md`](RELEASE_GATE.md), [`SECURITY.md`](SECURITY.md) and [`docs/OPERATIONS.md`](docs/OPERATIONS.md).
