# RAMS Release Gate

This checklist is mandatory before controlled production use. Do not skip gates to make a deployment look green.

## Gate 1: Local static checks

```bash
python -V
python -m compileall -q repo_mgmt tests
python -m pytest tests/ -q --tb=short
python -m ruff check .
python -m mypy repo_mgmt/ --no-incremental --show-error-codes
```

Expected result: all pass. If `mypy` cannot complete in a constrained assessment container, CI on a clean Linux runner is the source of truth.

## Gate 2: Docker image build and runtime binaries

```bash
docker build --target runtime -t rams-production-check .
docker run --rm rams-production-check python --version
docker run --rm rams-production-check git --version
docker run --rm rams-production-check node --version
docker run --rm rams-production-check npm --version
docker run --rm rams-production-check node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 20 ? 0 : 1)"
```

Expected result: Python, Git, Node.js 20+, and npm are available in the runtime image.

## Gate 3: Boot the production image

```bash
docker run --rm --env-file .env.example-dry-run -p 8000:8000 rams-production-check
```

Then, in another shell:

```bash
curl -sS http://localhost:8000/health
curl -sS -H "Authorization: Bearer $RMS_API_KEY" http://localhost:8000/readiness
```

Expected `/health`:

```json
{"status":"ok","pipelines":{"seo-aeo-geo":"idle","mobile-ux":"idle","on-brand":"idle"}}
```

With fake credentials and a valid bearer token, `/readiness` must be `degraded` and must show `r2_verified=false`. With real staging configuration, readiness must be `ready` before accepting pipeline triggers.

## Gate 4: Dry-run smoke tests

```bash
BASE_URL=http://localhost:8000 ./scripts/smoke_test.sh
```

Expected trigger response shape:

```json
{"runId":"<UTC>","pipeline":"<pipeline>","dryRun":true}
```

A duplicate trigger for a running pipeline must return:

```json
{"error":"pipeline already running","pipeline":"<pipeline>"}
```

## Gate 5: Koyeb dry-run staging

Required Koyeb settings:

- Deployment type: one web service
- Instance count: `1`
- Worker count: `1`
- Startup command: `rms-api`
- Exposed port: `8000`, or set `PORT` / `RMS_PORT` explicitly
- Health path: `/health`
- Readiness/operator check: `/readiness`
- `RMS_DRY_RUN=true`
- `RMS_LIVE_WRITE_ENABLED=false`
- `RMS_PUSH_ENABLED=false`
- `RMS_CREATE_PR=false`

Required mounted or cloned repos:

- `RMS_AIMS_REPO_PATH`
- `RMS_WEBSITE_REPO_PATH`

Both paths must exist. For live tests, each must be a clean Git worktree.

## Gate 6: Production dry-run monitoring

Run all three pipelines in dry-run mode against real R2 latest audit snapshots.

Verify:

- `/readiness` is `ready` before triggering.
- Run reports are written under `RMS_REPORT_DIR` in dry-run mode.
- `validation` is always an object, never `null`.
- Issue classifications are sane.
- Mobile UX never produces executable changes for protected content paths.
- On-brand editorial quality findings become `future_guidance`.
- API remains responsive when one pipeline fails.

## Gate 7: Throw-away-branch live commit test

Only after Gates 1-6 pass:

```bash
RMS_DRY_RUN=false
RMS_LIVE_WRITE_ENABLED=true
RMS_PUSH_ENABLED=false
RMS_CREATE_PR=false
```

First run the local primitive safety drill:

```bash
python scripts/disposable_live_branch_check.py
```

Then use a disposable target repo clone. Confirm:

- Branch creation uses `rms-qa/<pipeline>/<runId>`.
- The active branch is never `main` or `master` when writes occur.
- Validation runs before commit.
- Failed validation restores only task-scoped files.
- No unrelated dirty files are staged or modified.
- Push and PR automation remain disabled.

## Gate 8: Live push and PR decision

Live production writes remain NO-GO until repeated disposable-branch live commits prove validation, rollback, and exact-path staging. Live push remains NO-GO until separately authorised and credential-tested.

`RMS_CREATE_PR` remains NO-GO because PR creation is not implemented in this release.

## One-command local gate

```bash
./scripts/release_gate.sh
```

This script runs local Python gates and Docker gates. It exits with code `2` when Docker is unavailable so CI or a Docker-enabled Linux runner can execute the missing gate.
