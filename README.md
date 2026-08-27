# Repository Automation Management Service (RAMS)

RAMS is the controlled repository-remediation service for the website/AIMS estate. It is a Python/FastAPI application deployed on Koyeb with bounded model use, R2 evidence handling, branch-scoped repository writes and fail-closed validation.

## Pipelines

| Pipeline | Purpose |
|---|---|
| `website` | primary unified website remediation from AIMS `website-audit.json` |
| `content` | confirmed micro-surgery from the AIMS master content audit |
| `on-brand` | independent AIMS/on-brand remediation lane |
| `seo-aeo-geo` | legacy compatibility lane |
| `mobile-ux` | legacy compatibility lane |

The `content` lane is a first-class pipeline across configuration, schemas, API, CLI, repository bootstrap, audit reading, normalisation, remediation safety and reporting. Exact-key content runs target the AIMS repository and fail closed when the final AIMS content-audit key is absent or invalid.

## Remediation safety

RAMS reads governed audit evidence from R2, normalises only eligible findings, plans bounded changes and validates them before any live repository mutation. Live mode writes only to RAMS QA branches and can create/reuse a non-draft pull request. It does not auto-merge to `main`/`master`.

For the `content` lane, autonomous work is restricted to confirmed findings with exact existing affected paths and approved fix classes such as content-prompt, validator, council, retry, metadata, scheduler and link fixes. Anything ambiguous falls back to manual review.

## Main endpoints

| Endpoint | Auth | Purpose |
|---|---:|---|
| `GET /health` / `/livez` | No | lightweight process health |
| `GET /readiness` / `/readyz` | Bearer | dependency/repository/admission readiness |
| `GET /ops/warmup` | Bearer | local warm-up without repository mutation |
| `GET /ops/excellence` | Bearer | production controls/evidence |
| `GET /reports/*` | Bearer | bounded report access |
| `POST /rebuild/{pipeline_id}/run` | Bearer | run a governed remediation pipeline |

Website/content exact-key runs validate the supplied AIMS R2 key shape before work starts.

## Production controls

Live repository mutation requires dry-run disabled and live-write enabled. The current production profile intentionally keeps GitHub push and PR creation disabled, so validated changes stay inside the ephemeral checkout. Keep one worker and the global single-pipeline admission control unless concurrency has been deliberately re-profiled.

All non-secret production values are version-controlled in `Dockerfile` (with application-safe fallbacks in `repo_mgmt/config.py`). `RAMS-KOYEB-PRODUCTION-ENV.txt` contains only the required secret/sensitive Koyeb bindings.

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

## Production evidence and roadmap status

The repository contract for the final professional content-system audit and RAMS content hand-off is complete. Natural-run content evidence remains an operational monitoring activity in the separate content-production roadmap rows; it is not a missing RAMS implementation dependency.

See `SECURITY.md`, `docs/OPERATIONS.md`, `docs/OPERATIONAL_ALERTING.md` and `docs/OPTIMISATION_ENGINE.md`.
