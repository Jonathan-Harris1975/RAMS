# Repository Automation Management Service (RAMS)

RAMS is a clean-room, safety-first FastAPI service for controlled repository remediation. It exposes one HTTP API with three independent rebuild pipelines. Each pipeline reads an audit snapshot, normalises issues, plans bounded AnchorPatch/v1 changes, validates the target repo, and publishes a run report.

RAMS is safe by default: `RMS_DRY_RUN=true`, pushes disabled, PR creation disabled, and live writes blocked unless both live-write gates are explicitly opened.

## Pipelines

| Pipeline | Audit key | Target repo | Validation |
|---|---|---|---|
| SEO/AEO/GEO | `seo-aeo-geo` | AI Management Suite (Node.js / Express) | `npm test && npm run build` |
| Mobile UX | `mobile-ux` | jonathan-harris-website (static site) | `inject_partials`, `sync_redirects`, `check_crawlers` |
| On-Brand | `on-brand` | jonathan-harris-website (static site) | `inject_partials`, `sync_redirects`, `check_crawlers` |

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Exact public health contract: status plus pipeline states only |
| `GET` | `/readiness` | Dependency readiness detail for operators and deployment probes |
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

Dependency detail deliberately lives at `/readiness` so the `/health` contract stays aligned with the RMS specification.

### Trigger request

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
3. A clean Git worktree
4. A non-`main` / non-`master` active branch after QA branch creation
5. Single-worker / single-instance operation
6. Successful validation before commit

Pushes additionally require `RMS_PUSH_ENABLED=true`. `RMS_CREATE_PR=false` remains the default; PR creation is not treated as implemented by this service.

## Protected paths

The `mobile-ux` pipeline cannot touch:

- `blog/posts/`
- `blog/posts.json`
- `transcripts/`
- `data/podcast-episodes.json`
- `assets/js/podcast-transcripts.min.js`
- `functions/transcripts/`

These paths are blocked at both normalisation and patch-application layers.

## Dry-run report location

The original specification wrote dry-run reports to the current working directory. RAMS deliberately uses `RMS_REPORT_DIR` instead, defaulting to `/tmp/rams-reports`, because that is safer for containers and Koyeb-style deployments.

Override it when required:

```bash
RMS_REPORT_DIR=/app/reports rms dry-run on-brand
```

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

## External scheduling model

RAMS does not start an in-process cron scheduler. Trigger pipelines externally by HTTP, for example from Koyeb scheduled jobs, GitHub Actions, or another trusted scheduler. `repo_mgmt/scheduler.py` intentionally rejects accidental in-process scheduling.

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
pytest -q
ruff check .
mypy repo_mgmt
docker build --target runtime -t rams:production-ready .
docker run --rm --env-file .env.example-dry-run -p 8000:8000 rams:production-ready
```

## Provenance

See [`CLEAN_ROOM_PROVENANCE.md`](CLEAN_ROOM_PROVENANCE.md). RAMS is built from the RMS specification only. The separate reference archive is excluded from source, Docker context, CI, and release artefacts.
