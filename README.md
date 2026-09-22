# Repository Automation Management Service (RAMS)

RAMS is the controlled repository-remediation service for the website/AIMS estate. It is a Python/FastAPI application deployed on Koyeb with bounded model use, authenticated Cloudflare R2 evidence storage, branch-scoped repository mutation and fail-closed validation.

RAMS accepts governed audit evidence, turns eligible findings into tightly bounded remediation work, materialises or refreshes the target repository, plans and validates changes, and publishes reports/evidence. It is deliberately not a general-purpose unattended code agent.

## Architecture and responsibilities

The main application surface is `repo_mgmt/api.py`, with CLI/direct execution support in `repo_mgmt/cli.py` and pipeline orchestration in `repo_mgmt/pipeline.py`. Repository bootstrap/indexing, issue normalisation, patch planning/application, validation, report publication, model governance and operational controls are split into focused modules under `repo_mgmt/`.

Cloudflare R2 is the authenticated persistence layer for governed audit inputs, RAMS reports, selected operational evidence and persisted model-governance state. Target source repositories remain Git repositories; R2 is not used as a source-code store.

The historical in-process cron scheduler is intentionally retired. `repo_mgmt/scheduler.py` exists only as a compatibility guard and raises if code attempts to create an in-process scheduler. Production triggers work externally through `POST /rebuild/{pipeline_id}/run`.

## Pipelines

| Pipeline | Purpose |
|---|---|
| `website` | primary unified website remediation from AIMS `website-audit.json` |
| `content` | confirmed micro-surgery from the AIMS master content audit |
| `on-brand` | independent AIMS/on-brand remediation lane |
| `seo-aeo-geo` | retained legacy compatibility lane |
| `mobile-ux` | retained legacy compatibility lane |

The `content` lane is a first-class pipeline across configuration, schemas, API, CLI, repository bootstrap, audit reading, normalisation, remediation safety and reporting. Exact-key content runs target the AIMS repository and fail closed when the final AIMS content-audit key is absent or invalid.

## Remediation safety

RAMS reads governed audit evidence from R2, normalises only eligible findings, plans bounded changes and validates them before any live repository mutation. RAMS never writes directly to `main`/`master`.

The current production profile permits governed changes only inside the ephemeral checkout and intentionally disables GitHub publication with `RMS_PUSH_ENABLED=false` and `RMS_CREATE_PR=false`. If a future reviewed deployment enables publication, RAMS is restricted to its configured `rms-qa/*` branch and non-draft pull requests; it never auto-merges.

For the `content` lane, autonomous work is restricted to confirmed findings with exact existing affected paths and approved fix classes such as content-prompt, validator, council, retry, metadata, scheduler-related configuration and link fixes. “Scheduler” findings are remediation categories; they do not re-enable the retired RAMS in-process scheduler. Anything ambiguous falls back to manual review.

RAMS capability metadata is repository-local under `config/skills` and uses stable `RAMS-skNNN` identifiers. It describes native code only; there is no shared skills bucket, external descriptor fetch or runtime skill installer. See `RAMS_LOCAL_CAPABILITIES.md`.

## R2 configuration and failure behaviour

RAMS requires the R2/S3-compatible endpoint, audits bucket and credentials to be supplied through the configured environment/Koyeb contract:

```env
R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID={{ secret.R2_ACCESS_KEY_ID }}
R2_SECRET_ACCESS_KEY={{ secret.R2_SECRET_ACCESS_KEY }}
R2_REGION=auto
R2_BUCKET_AUDITS=audits
```

Never put live credentials in repository examples, logs, API responses or test fixtures. `R2Client` uses short network timeouts and the botocore standard retry policy with one retry after the initial attempt. Construction proves only that local configuration can be consumed; live reachability is established by `HeadBucket` through `verify_bucket()`.

`/health` and `/livez` remain process-liveness checks. R2 endpoint, credential, permission, network or bucket failures cause authenticated readiness/operational checks to report a degraded state rather than taking liveness down.

R2 SDK/transport failures are translated to the domain-specific `R2Error`. Error text is reduced to a credential-safe exception/code/status summary rather than raw provider messages. Object-not-found codes remain distinguishable where callers need 404 behaviour. Bounded read paths use `get_object_limited()` and reject objects that exceed configured limits; response streams are closed on success and failure. Production report, audit-evidence and persisted model-governance reads use bounded paths. Compatibility fallbacks retained for older test/double interfaces apply their own post-read limit where relevant.

## Main endpoints

| Endpoint | Auth | Purpose |
|---|---:|---|
| `GET /health` / `/livez` | No | lightweight process health |
| `GET /readiness` / `/readyz` | Bearer | dependency/repository/admission readiness |
| `GET /ops/warmup` | Bearer | local warm-up without repository mutation or external work |
| `GET /ops/excellence` | Bearer | production controls/evidence |
| `POST /ops/model-governance/apply` | Bearer | validate, persist and activate HIVE model selections |
| `GET /reports/*` | Bearer | bounded report access |
| `POST /rebuild/{pipeline_id}/run` | Bearer | run a governed remediation pipeline |

Website/content exact-key runs validate the supplied AIMS R2 key shape before work starts.

## Production controls

Live repository mutation requires dry-run disabled and live-write enabled. The current production profile intentionally keeps GitHub push and PR creation disabled, so validated changes stay inside the ephemeral checkout. RAMS idempotency and admission state are process-local, so production must keep exactly one worker and one deployment instance. Readiness degrades and all runs are rejected if that contract is violated; horizontal scale-out requires a shared idempotency/admission store first.

All non-secret production values are version-controlled in `Dockerfile` (with application-safe fallbacks in `repo_mgmt/config.py`). `RAMS-KOYEB-PRODUCTION-ENV.txt` contains only the required secret/sensitive Koyeb bindings.

## CLI

RAMS exposes the Typer-based `rms` console command after package installation. It is an operator/developer interface to the same governed pipeline layer used by the API; it does not bypass configuration, dry-run, repository, validation or publication controls.

```bash
rms --help
rms dry-run on-brand
rms dry-run website --audit-json-key audits/website/2026-07/SESSION_ID/website-audit.json
rms run on-brand --dry-run
rms run content --audit-json-key audits/content-master/2026-07/SESSION_ID/content-audit.json --dry-run
```

Supported pipeline IDs are `website`, `content`, `seo-aeo-geo`, `mobile-ux` and `on-brand`. `website` and `content` require an exact final audit object key. `dry-run` always disables writes/commits/pushes; `run` follows `RMS_DRY_RUN` unless `--dry-run` or `--no-dry-run` overrides it. Configuration-load failures exit with status 1, missing required audit keys exit with status 2, and Typer reports invalid command/argument usage with its normal non-zero CLI exit semantics.

The CLI requires the same environment and secret configuration as the service for whichever dependencies the selected pipeline uses. The hash-locked development installation below installs both `rms` and `rms-api` console entry points.

## Development and validation

Create a clean environment and install against the compiled production lock:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements-bootstrap.txt
python -m pip install --require-hashes -r requirements-dev.txt
python -m pip install --no-index --no-deps --no-build-isolation -e .
python -m pip check
```

Run the local release-quality checks:

```bash
python -m compileall -q repo_mgmt tests scripts/emicro_benchmark.py
python scripts/verify_dependency_lock.py --compile
python scripts/verify_hash_enforcement.py
python scripts/secret_scan.py
python -m pytest tests/ -q --tb=short
python -m pytest --cov=repo_mgmt --cov-report=term-missing -q
python -m ruff check .
python -m mypy repo_mgmt/ --no-incremental --show-error-codes
python -m bandit -q -r repo_mgmt -ll
python -m pip_audit
python scripts/disposable_live_branch_check.py
python scripts/emicro_benchmark.py --label candidate
```

`tests/test_r2_client.py` contains deterministic R2 contract coverage for readiness, access denial/missing buckets, bounded reads, not-found and non-404 object-head failures, uploads/content type, SDK/transport error translation, secret-safe diagnostics and the configured SDK retry path. These tests use botocore `Stubber`, mocks, dummy credentials and a local retry endpoint; they never require production R2 credentials.

Optional live-storage smoke checks belong in an explicitly authorised non-production environment. Verify `/readiness`/`/readyz`, perform one bounded read/write against the intended test bucket, and remove the test object afterwards. Do not convert live storage access into a mandatory unit-test dependency.

## Dependency architecture

RAMS keeps four reviewable source/lock pairs: runtime (`requirements.in` / `requirements.txt`), packaging bootstrap (`requirements-bootstrap.in` / `requirements-bootstrap.txt`), PEP 517 build tooling (`requirements-build.in` / `requirements-build.txt`) and CI/development (`requirements-dev.in` / `requirements-dev.txt`). Every lock is generated by pip-tools, pins exact versions and carries SHA-256 hashes for the available distribution artefacts. `pyproject.toml` reads runtime dependency metadata dynamically from `requirements.in`.

Docker, CI and `scripts/install_production.sh` install third-party packages with `--require-hashes`. The local RAMS package is then built offline with `--no-index --no-deps --no-build-isolation`, using the separately hash-locked build tool. Use `--upgrade` only during an intentional, reviewed dependency update.

After an intentional production dependency change, regenerate and verify the lock rather than editing generated dependency state ad hoc:

```bash
python -m piptools compile --generate-hashes --allow-unsafe --no-emit-index-url --no-emit-trusted-host --no-strip-extras --output-file=requirements.txt requirements.in
python -m piptools compile --generate-hashes --allow-unsafe --no-emit-index-url --no-emit-trusted-host --no-strip-extras --output-file=requirements-bootstrap.txt requirements-bootstrap.in
python -m piptools compile --generate-hashes --allow-unsafe --no-emit-index-url --no-emit-trusted-host --no-strip-extras --output-file=requirements-build.txt requirements-build.in
python -m piptools compile --generate-hashes --allow-unsafe --no-emit-index-url --no-emit-trusted-host --no-strip-extras --output-file=requirements-dev.txt requirements-dev.in
python scripts/verify_dependency_lock.py --compile
python scripts/verify_hash_enforcement.py
```

Do not reintroduce `requirements.lock`; production, CI and packaging must stay on the same Dependabot-visible dependency path.

## Deployment and operations

Canonical runtime/deployment guidance is in `docs/OPERATIONS.md`, `docs/PRODUCTION_DEPLOYMENT_CHECKLIST.md` and `RELEASE_GATE.md`. Production recovery starts with public liveness, then authenticated readiness/excellence evidence, followed by configuration repair and a safe dry-run before live-write admission is restored.

`HIVE_REPOSITORY_METADATA.json` exposes the canonical HIVE ID `RAMS` and current architecture, dependency, deployment and operations paths for Repository Memory/Intelligence ingestion.

Logs and operational events must remain redacted. Secret scanning, dependency-lock verification, dependency vulnerability auditing, Bandit, linting, typing, tests/coverage and Docker/API smoke checks are release gates.

The automatic post-CI production watcher also fails closed: GitHub Actions must provide `KOYEB_TOKEN` and `KOYEB_SERVICE`, the bounded Koyeb watch must observe the expected source SHA, and only then may the workflow retain a production deployment attestation and dispatch the required ecosystem smoke. Missing watcher configuration is a release-verification failure, not an optional skip. The credential-free contract is covered by `tests/test_deployment_watch_workflow.py` and runs with the normal pytest suite.

## Production evidence and roadmap status

The repository contract for the final professional content-system audit and RAMS content hand-off is complete. Natural-run content evidence remains an operational monitoring activity in the separate content-production roadmap rows; it is not a missing RAMS implementation dependency.

See `SECURITY.md`, `docs/OPERATIONS.md`, `docs/MODEL_GOVERNANCE.md`, `docs/OPERATIONAL_ALERTING.md` and `docs/OPTIMISATION_ENGINE.md`.
