# Repository Automation Management Service (RAMS)

RAMS is a clean-room, safety-first FastAPI service for controlled repository remediation. It exposes one HTTP API with three independent rebuild pipelines. Each pipeline reads an audit snapshot, normalises issues, plans bounded AnchorPatch/v1 changes, validates the target repo, and publishes a run report.

RAMS is safe by default: `RMS_DRY_RUN=true`, live writes disabled, pushes disabled, PR creation disabled, and production writes blocked unless both live-write gates are explicitly opened.

## Pipelines

| Pipeline | Audit key | Target repo | Validation |
|---|---|---|---|
| SEO/AEO/GEO | `seo-aeo-geo` | `jonathan-harris-website`, static site | `inject_partials`, `sync_redirects`, `check_crawlers` |
| Mobile UX | `mobile-ux` | `jonathan-harris-website`, static site | `inject_partials`, `sync_redirects`, `check_crawlers` |
| On-Brand | `on-brand` | AIMS / AI Management Suite, Node.js / Express | `npm test && npm run build` |

The production Docker image includes Python, Git, Node.js 20+, and npm so both website checks and AIMS validation can run inside the deployed container.

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness only: exact RMS health contract, with pipeline states only |
| `GET` | `/readiness` | Authenticated dependency readiness for operators and pre-trigger deployment checks |
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

`/readiness` is operator-only and requires `Authorization: Bearer $RMS_API_KEY`. It distinguishes local configuration from real dependency verification:

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
    "website_repo_ready": true,
    "aims_repo_ready": true,
    "pipeline_repo_paths": {
      "seo-aeo-geo": "/tmp/rams-repos/website",
      "mobile-ux": "/tmp/rams-repos/website",
      "on-brand": "/tmp/rams-repos/aims"
    },
    "repo_bootstrap": {
      "enabled": true,
      "attempted": true,
      "results": []
    },
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

All non-health endpoints are protected. `/` and `/health` remain public for liveness probes; `/readiness`, `/reports/*`, and `/rebuild/*` require `RMS_API_KEY` by default. Send it as a Bearer token:

```bash
curl -sS -X POST "$BASE_URL/rebuild/mobile-ux/run" \
  -H "Authorization: Bearer $RMS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"dry_run":true}'
```

If `RMS_API_KEY` is not configured, protected endpoints fail closed with `503 Service Unavailable`. Local-only developer runs may opt in to unauthenticated triggers with `RMS_ALLOW_UNAUTHENTICATED_DEV=true`; do not use that override on a public service.

`dry_run` is optional. If omitted, RAMS uses `RMS_DRY_RUN`.

### `202 Accepted`

The response body and the `X-Run-Id` response header both carry the run ID.

```json
{
  "runId": "2026-05-05T03-00-00Z",
  "pipeline": "mobile-ux",
  "dryRun": true
}
```

`X-Run-Id: 2026-05-05T03-00-00Z` is also present as a response header so callers can read the run ID without parsing JSON.

### `401 Unauthorized`

Returned when the request carries a missing or incorrect Bearer token.

```json
{"error": "unauthorized"}
```

### `503 Service Unavailable`

Returned when `RMS_API_KEY` is missing and unauthenticated developer mode is not explicitly enabled.

```json
{
  "error": "RMS_API_KEY is required for protected endpoints",
  "hint": "Set RMS_API_KEY for deployed use, or set RMS_ALLOW_UNAUTHENTICATED_DEV=true for local-only development."
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

Pushes additionally require `RMS_PUSH_ENABLED=true`. Keep `RMS_CREATE_PR=false` until a push-only run has proved the branch appears on GitHub; only then test PR creation deliberately.

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


## Audit manifest dereferencing

Production audit `latest.json` files are lightweight manifests. RAMS now reads the manifest first, then follows the JSON artefact pointers it exposes so the normaliser can see the actual finding ledgers.

Supported child artefacts include:

- `mobile-ux`: `repository-issue-appendix.json`, `responsive-fix-appendix.json`, `mandatory-mobile-scorecard.json`, `focused-page-appendix.json`, `summary.json`, `coverage.json`, `report.json`
- `seo-aeo-geo`: `summary.json`, `coverage.json`, `report.json`, `evidence.json`
- `on-brand`: `report.json`, `evidence.json`, `summary.json`

RAMS ignores non-JSON evidence such as screenshots during task generation. Missing child artefacts are logged and recorded in the enriched audit payload, but they do not abort the run. Extracted findings are capped per run to avoid creating huge dry-run reports from screenshot-heavy audits.

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

### Target repo mapping

RAMS uses this live mapping:

```text
seo-aeo-geo -> RMS_WEBSITE_REPO_PATH
mobile-ux   -> RMS_WEBSITE_REPO_PATH
on-brand    -> RMS_AIMS_REPO_PATH
```

For Koyeb, where target repos are not mounted by default, enable runtime bootstrap and provide Git URLs:

```text
RMS_REPO_BOOTSTRAP_ENABLED=true
RMS_REPO_BASE_DIR=/tmp/rams-repos
RMS_WEBSITE_REPO_URL=<website-repo-git-url>
RMS_WEBSITE_REPO_BRANCH=main
RMS_WEBSITE_REPO_PATH=/tmp/rams-repos/website
RMS_AIMS_REPO_URL=<aims-repo-git-url>
RMS_AIMS_REPO_BRANCH=main
RMS_AIMS_REPO_PATH=/tmp/rams-repos/aims
RMS_GITHUB_TOKEN=<token-with-read-access-if-private>
# GITHUB_TOKEN is also supported as a backwards-compatible alias
```

The bootstrapper clones or refreshes the two target repos on the first rebuild trigger. It does not run during `/health`.

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
- Set `RMS_API_KEY` to a strong random secret before exposing protected endpoints
- Keep `RMS_ALLOW_UNAUTHENTICATED_DEV=false` on deployed services
- Set real R2 and OpenRouter values
- Set `RMS_WEBSITE_REPO_PATH` for `seo-aeo-geo` and `mobile-ux`
- Set `RMS_AIMS_REPO_PATH` for `on-brand`
- For Koyeb, set `RMS_REPO_BOOTSTRAP_ENABLED=true`, `RMS_WEBSITE_REPO_URL`, `RMS_AIMS_REPO_URL`, and `RMS_GITHUB_TOKEN` when repos are private. `GITHUB_TOKEN` remains supported as a backwards-compatible alias
- Ensure target repo paths exist in the container, or let the bootstrapper clone them on the first rebuild trigger

Do not treat a green `/health` as deployment readiness. The little green lamp only proves the process is alive; `/readiness` is where the grown-up machinery reports its actual state. 🛠️

## Provenance

See [`CLEAN_ROOM_PROVENANCE.md`](CLEAN_ROOM_PROVENANCE.md). RAMS is built from the RMS specification only. The separate reference archive is excluded from source, Docker context, CI, and release artefacts.
