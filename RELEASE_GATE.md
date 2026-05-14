# RAMS Release Gate

This checklist is mandatory before controlled production use. Do not skip gates to make a deployment look green.

## Gate 1: Local static checks

```bash
python -m compileall -q repo_mgmt tests
pytest -q
ruff check .
mypy repo_mgmt
```

Expected result: all pass.

## Gate 2: Docker image build

```bash
docker build --target runtime -t rams:production-ready .
```

Expected result: image builds without warnings that affect runtime dependencies.

## Gate 3: Boot the production image

```bash
docker run --rm --env-file .env.example-dry-run -p 8000:8000 rams:production-ready
```

Then, in another shell:

```bash
curl -sS http://localhost:8000/health
curl -sS http://localhost:8000/readiness
```

Expected `/health`:

```json
{"status":"ok","pipelines":{"seo-aeo-geo":"idle","mobile-ux":"idle","on-brand":"idle"}}
```

`/readiness` may be `degraded` when placeholder paths or credentials are used. With real staging configuration, readiness must be `ready` before accepting pipeline triggers.

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
- Exposed port: `8000`
- Health path: `/health`
- Readiness/operator check: `/readiness`
- `RMS_DRY_RUN=true`
- `RMS_LIVE_WRITE_ENABLED=false`
- `RMS_PUSH_ENABLED=false`
- `RMS_CREATE_PR=false`

Required mounted or cloned repos:

- `RMS_SEO_REPO_PATH`
- `RMS_WEBSITE_REPO_PATH`

Both paths must exist. For live tests, each must be a clean Git worktree.

## Gate 6: Production dry-run monitoring

Run all three pipelines in dry-run mode against real R2 latest audit snapshots.

Verify:

- Run reports are written under `RMS_REPORT_DIR` in dry-run mode.
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

Use a disposable branch and disposable target repo clone. Confirm:

- The active branch is never `main` or `master` when writes occur.
- Validation runs before commit.
- Failed validation restores only task-scoped files.
- No unrelated dirty files are staged or modified.

## Gate 8: Live push decision

Live push remains NO-GO until throw-away-branch live commits are repeatedly proven and push credentials are tested safely.

Only then consider:

```bash
RMS_PUSH_ENABLED=true
```

`RMS_CREATE_PR` remains NO-GO unless PR creation is separately implemented and tested.
