# Repository automation management service(RAMS)


A clean-room, production-grade autonomous repository management service that exposes
an HTTP API. Each endpoint triggers an independent, self-contained audit pipeline.

## Architecture

Three independent pipelines share library modules but have their own:
- Target repository
- Validation commands
- Protected path rules
- Approved fix classes

| Pipeline | Audit Key | Target Repo | Validation |
|---|---|---|---|
| SEO/AEO/GEO | `seo-aeo-geo` | AI Management Suite (Node.js) | `npm test && npm run build` |
| Mobile UX | `mobile-ux` | jonathan-harris-website (static) | inject_partials, sync_redirects, check_crawlers |
| On-Brand | `on-brand` | jonathan-harris-website (static) | inject_partials, sync_redirects, check_crawlers |

## HTTP API

| Method | Path | Description |
|---|---|---|
| `POST` | `/rebuild/seo-aeo-geo/run` | Trigger SEO pipeline |
| `POST` | `/rebuild/mobile-ux/run` | Trigger Mobile UX pipeline |
| `POST` | `/rebuild/on-brand/run` | Trigger On-Brand pipeline |
| `GET` | `/health` | Health check — all pipelines |

### POST body (optional)
```json
{ "dry_run": true }
```

### 202 Accepted response
```json
{ "runId": "2026-05-05T03-00-00Z", "pipeline": "on-brand", "dryRun": true }
```

### 409 Conflict (pipeline already running)
```json
{ "error": "pipeline already running", "pipeline": "on-brand" }
```

## Setup

```bash
cp .env.template .env
# Fill in all required values in .env

pip install -e .
```

## Running

```bash
# Start the API server
rms-api

# Or with uvicorn directly
uvicorn repo_mgmt.api:app --host 0.0.0.0 --port 8000

# Dry-run a specific pipeline via CLI
rms dry-run seo-aeo-geo
rms dry-run mobile-ux
rms dry-run on-brand
```

## Safety Defaults

- `RMS_DRY_RUN=true` — never writes or commits by default
- `RMS_PUSH_ENABLED=false` — branch push disabled by default
- `RMS_CREATE_PR=false` — PR creation disabled by default
- `BranchSafetyError` raised if active branch is `main` or `master`

## Testing

```bash
pytest tests/ -v
```

## Provenance

See `CLEAN_ROOM_PROVENANCE.md`. All code is derived from the RMS specification only.
