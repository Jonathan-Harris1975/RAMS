# Repository Automation Management Service (RAMS)

RAMS is a clean-room, safety-first FastAPI service for controlled repository remediation. It exposes one HTTP API with three independent rebuild pipelines. Each pipeline reads an audit snapshot, normalises issues, plans bounded AnchorPatch/v1 changes, validates the target repo, and publishes a run report.

RAMS is safe by default: `RMS_DRY_RUN=true`, live writes disabled, pushes disabled, PR creation disabled, and production writes blocked unless both live-write gates are explicitly opened.

## Pipelines

| Pipeline | Audit key | Target repo | Validation |
|---|---|---|---|
| SEO/AEO/GEO | `seo-aeo-geo` | AI Management Suite, Node.js / Express | `npm test && npm run build` |
| Mobile UX | `mobile-ux` | `jonathan-harris-website`, static site | `inject_partials`, `sync_redirects`, `check_crawlers` |
| On-Brand | `on-brand` | `jonathan-harris-website`, static site | `inject_partials`, `sync_redirects`, `check_crawlers` |

The production Docker image includes Python, Git, Node.js 20+, and npm so the SEO/AEO/GEO validation command can run inside the deployed container.

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness only: exact RMS health contract, with pipeline states only |
| `GET` | `/readiness` | Dependency readiness for operators and pre-trigger deployment checks |
| `POST` | `/rebuild/seo-aeo-geo/run` | Trigger SEO/AEO/GEO pipeline |
| `POST` | `/rebuild/mobile-ux/run` | Trigger Mobile UX pipeline |
| `POST` | `/rebuild/on-brand/run` | Trigger On-Brand pipeline |

### `/health` response

```json
{
  "status": "ok",
  "pipelines": {
    "seo-aeo-geo": "idle",
    "mobile-ux": "idle",
    "on-brand": "idle"
  }
}
```

`/health` deliberately does not check R2, OpenRouter, mounted repos, Node, npm, or Git. Use it for Koyeb liveness only.

### `/readiness` response

`/readiness` distinguishes local configuration from real dependency verification:

```json
{
  "status": "ready",
  "pipelines": {
    "seo-aeo-geo": "idle",
    "mobile-ux": "idle",
    "on-brand": "idle"
  },
  "dependencies": {
    "config_loaded": true,
    "r2_configured": true,
    "r2_verified": true,
    "seo_repo_ready": true,
    "website_repo_ready": true,
    "validation_runtime_ready": true,
    "model_router_ready": true,
    "single_worker_mode": true,
    "runtime": {
      "python": "Python 3.11.x",
      "git": "git version ...",
      "node": "v20.x or newer",
      "npm": "..."
    }
  }
}
```

A fake R2 endpoint or invalid credentials must leave `r2_verified=false` and `status=degraded`. Pipeline triggers are refused while R2 is not verified.

## Trigger request

```bash
curl -sS -X POST "$BASE_URL/rebuild/mobile-ux/run" \
  -H "Content-Type: application/json" \
  -d '{"dry_run":true}'
```

`dry_run` is optional. If omitted, RAMS uses `RMS_DRY_RUN`.

### `202 Accepted`

```json
{
  "runId": "2026-05-05T03-00-00Z",
  "pipeline": "mobile-ux",
  "dryRun": true
}
```

### `409 Conflict`

```json
{
  "error": "pipeline already running",
  "pipeline": "mobile-ux"
}
```

## Safety gates

Live writes require all of the following:

1. `RMS_DRY_RUN=false`
2. `RMS_LIVE_WRITE_ENABLED=true`
3. `/readiness` is `ready`
4. A clean Git worktree
5. A QA branch under `rms-qa/<pipeline>/<runId>`
6. A non-`main` / non-`master` active branch before any write, stage, commit, or push
7. Single-worker / single-instance operation
8. Successful validation before commit

Pushes additionally require `RMS_PUSH_ENABLED=true`. `RMS_CREATE_PR=false` remains the required setting because PR creation is not implemented in this release.

## Protected paths

The `mobile-ux` pipeline cannot touch:

- `blog/posts/`
- `blog/posts.json`
- `transcripts/`
- `data/podcast-episodes.json`
- `assets/js/podcast-transcripts.min.js`
- `functions/transcripts/`

These paths are blocked at both normalisation and patch-application layers.

The `on-brand` pipeline may patch blog/transcript files only for deterministic structural or metadata defects. Editorial quality findings for historical content become `future_guidance` and must not reach patch application.

## RunReport validation object

Run reports always serialise `validation` as an object with:

- `commands`
- `passed`
- `outputTail`

When validation has not run, the report emits a consumer-safe not-run object rather than `null`.

## Dry-run report location

Accepted production deviation: the original RMS prompt wrote dry-run reports to the current working directory. RAMS deliberately uses `RMS_REPORT_DIR`, defaulting to `/tmp/rams-reports`, because that is safer for containers and Koyeb-style deployments.

Override it when required:

```bash
RMS_REPORT_DIR=/app/reports rms dry-run on-brand
```

## External scheduling model

Accepted production deviation: RAMS does not start an in-process cron scheduler. Trigger pipelines externally by HTTP, for example from Koyeb scheduled jobs, GitHub Actions, or another trusted scheduler. `repo_mgmt/scheduler.py` intentionally rejects accidental in-process scheduling.

## Local setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
cp .env.template .env
```

Fill in real R2, OpenRouter, and target repo path values before running real dry-runs.

## Run the API

```bash
rms-api
# or
uvicorn repo_mgmt.api:app --host 0.0.0.0 --port 8000
```

`rms-api` honours `RMS_PORT` first, then `PORT`, then the configured default `8000`.

## Smoke tests

Use the included script:

```bash
BASE_URL=http://localhost:8000 ./scripts/smoke_test.sh
```

Or run the equivalent calls manually:

```bash
curl -sS "$BASE_URL/health"
curl -sS "$BASE_URL/readiness"
curl -sS -X POST "$BASE_URL/rebuild/seo-aeo-geo/run" -H "Content-Type: application/json" -d '{"dry_run":true}'
curl -sS -X POST "$BASE_URL/rebuild/mobile-ux/run" -H "Content-Type: application/json" -d '{"dry_run":true}'
curl -sS -X POST "$BASE_URL/rebuild/on-brand/run" -H "Content-Type: application/json" -d '{"dry_run":true}'
```

A duplicate POST while the same pipeline is running must return `409`.

## Release gates

See [`RELEASE_GATE.md`](RELEASE_GATE.md). The short version:

```bash
python -m compileall -q repo_mgmt tests
python -m pytest tests/ -q --tb=short
python -m ruff check .
python -m mypy repo_mgmt/ --no-incremental --show-error-codes
docker build --target runtime -t rams-production-check .
docker run --rm rams-production-check python --version
docker run --rm rams-production-check git --version
docker run --rm rams-production-check node --version
docker run --rm rams-production-check npm --version
```

`./scripts/release_gate.sh` runs the same local gate plus Docker smoke checks when Docker is available.

## Koyeb deployment notes

- Deployment type: one web service
- Instance count: `1`
- Worker count: `1`
- Startup command: `rms-api`
- Liveness path: `/health`
- Operator readiness check: `/readiness`
- Keep `RMS_DRY_RUN=true` for staging
- Keep `RMS_LIVE_WRITE_ENABLED=false`
- Keep `RMS_PUSH_ENABLED=false`
- Keep `RMS_CREATE_PR=false`
- Set real R2, OpenRouter, `RMS_SEO_REPO_PATH`, and `RMS_WEBSITE_REPO_PATH`
- Ensure target repo paths exist in the container or attached runtime filesystem

Do not treat a green `/health` as deployment readiness. The little green lamp only proves the process is alive; `/readiness` is where the grown-up machinery reports its actual state. 🛠️

## Provenance

See [`CLEAN_ROOM_PROVENANCE.md`](CLEAN_ROOM_PROVENANCE.md). RAMS is built from the RMS specification only. The separate reference archive is excluded from source, Docker context, CI, and release artefacts.
