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

The public API accepts `content`, and the pipeline/audit-reader/normaliser contain content-lane logic. There is, however, a current type-schema inconsistency: `repo_mgmt/schemas.py` still defines `PipelineId` without `content`, while `repo_mgmt/config.py`, `api.py` and `pipeline.py` include it. This must be corrected and covered by a content-pipeline regression test before the content remediation lane can be treated as fully closed.

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

Live repository mutation requires the explicit live settings, including dry-run disabled, live-write enabled, push enabled and PR creation enabled. Keep one worker and the global single-pipeline admission control unless concurrency has been deliberately re-profiled.

The exact production values are documented in `RAMS-KOYEB-PRODUCTION-ENV.txt` and `repo_mgmt/config.py`. Secrets belong in the deployment secret store.

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

The machinery for the final professional content-system audit and RAMS content hand-off is substantially present. The roadmap item still requires real production evidence from the content lanes before the final audit can be considered complete, and the `PipelineId` schema drift above should be fixed first.

See `SECURITY.md`, `docs/OPERATIONS.md`, `docs/OPERATIONAL_ALERTING.md` and `docs/OPTIMISATION_ENGINE.md`.
