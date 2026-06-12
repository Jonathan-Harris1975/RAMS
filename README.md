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
| `GET` | `/ops/warmup` | Authenticated local warm-up; creates bounded HTTP clients without external requests or operational work |
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

A fake R2 endpoint or invalid credentials must leave `r2_verified=false` and `status=degraded`. Pipeline triggers are refused while R2 is not verified. Runtime version probes are cached for `RMS_READINESS_CACHE_SECONDS` so repeated readiness checks do not keep spawning subprocesses on a quarter-vCPU instance.

### `/ops/warmup` contract

`/ops/warmup` follows the AIMS operational warm-up principle: an ops request warms infrastructure, not business processing. It loads validated configuration and creates RAMS's tiny reusable HTTP client pools. It deliberately does **not** clone or refresh repositories, read R2, load audits, run validation, or call OpenRouter.

```bash
curl -sS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/ops/warmup"
```

The endpoint is suitable for a trusted pre-trigger warm-up. It must never be used as a hidden audit trigger.

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

MAST already sends both `X-Trigger-Run-Key` and `X-Idempotency-Key`. RAMS accepts either header and stores a small in-process replay cache. A retry with the same run key returns the original `202` admission and run ID instead of launching duplicate work. The cache is intentionally bounded by `RMS_IDEMPOTENCY_CACHE_SIZE`; it is process-local and is not a distributed queue.

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

### `409 Conflict` and `429 Too Many Requests`

A duplicate request for the already active pipeline receives `409`. A request for a different pipeline while any RAMS pipeline is active receives `429`. Both include `Retry-After`, because eco-micro permits only one heavyweight pipeline globally.

```json
{
  "error": "RAMS is busy",
  "pipeline": "on-brand",
  "activePipeline": "mobile-ux",
  "activeRunId": "2026-06-12T12-00-00Z"
}
```

## HIVE shared skill pool

RAMS no longer carries a local `.agents` skill library. HIVE controls the shared skill pool in Cloudflare R2, and RAMS consumes the RAMS manifest in read-only mode.

```text
R2_PUBLIC_BASE_URL_HIVE_SKILLS=https://pub-da50a6512f164566955a3076a1c795ef.r2.dev
R2_BUCKET_HIVE_SKILLS=hive-skills
```

RAMS expects the approved skill manifest at:

```text
https://pub-da50a6512f164566955a3076a1c795ef.r2.dev/manifests/rams-skills-manifest.json
```

Central skill metadata is report/planning context only. It does not authorise local marketplace installs, direct skill execution, auto-merge, auto-deploy, or Cloudflare/DNS mutation.

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

RAMS ignores non-JSON evidence such as screenshots during task generation. Missing child artefacts are logged and recorded in the enriched audit payload, but they do not abort the run. Extracted findings are capped per run to avoid creating huge dry-run reports from screenshot-heavy audits. R2 reads are additionally bounded by object size, total evidence bytes and artefact count through the `RMS_MAX_AUDIT_*` settings.

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

Accepted production deviation: RAMS does not start an in-process cron scheduler. MAST remains the external scheduler and already spaces the monthly RAMS rebuild/report jobs. Trigger pipelines externally by authenticated HTTP. `repo_mgmt/scheduler.py` intentionally rejects accidental in-process scheduling. MAST run-key headers are used for idempotent replay, while a busy RAMS response carries `Retry-After`. No MAST source change is required for the current contract.

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
BASE_URL=http://localhost:8000 RMS_API_KEY="$RMS_API_KEY" ./scripts/smoke_test.sh
```

Or run the equivalent calls manually:

```bash
curl -sS "$BASE_URL/health"
curl -sS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/readiness"
curl -sS -H "Authorization: Bearer $RMS_API_KEY" "$BASE_URL/ops/warmup"
curl -sS -X POST "$BASE_URL/rebuild/seo-aeo-geo/run" -H "Authorization: Bearer $RMS_API_KEY" -H "Content-Type: application/json" -d '{"dry_run":true}'
curl -sS -X POST "$BASE_URL/rebuild/mobile-ux/run" -H "Authorization: Bearer $RMS_API_KEY" -H "Content-Type: application/json" -d '{"dry_run":true}'
curl -sS -X POST "$BASE_URL/rebuild/on-brand/run" -H "Authorization: Bearer $RMS_API_KEY" -H "Content-Type: application/json" -d '{"dry_run":true}'
```

A duplicate POST while the same pipeline is running must return `409`; a different pipeline must return `429`. Reusing an accepted MAST idempotency key must return the original `202` run ID.

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

## Koyeb eco-micro deployment notes

RAMS is now tuned specifically for Koyeb `eco-micro`: 0.25 vCPU, 512MB RAM and 4GB ephemeral SSD. This is a single-lane road, not the M25.

- Deployment type: one web service
- Instance type: `eco-micro`
- Instance count: `1`
- Worker count: `1`; `rms-api` also hard-codes one Uvicorn worker
- Startup command: `rms-api`
- Liveness path: `/health`
- Operator readiness path: `/readiness`
- Optional trusted warm-up path: `/ops/warmup`
- Keep `RMS_MAX_CONCURRENT_PIPELINES=1` and `RMS_MAX_ISSUES_PER_RUN=1`
- Keep `WEB_CONCURRENCY=1`, `UVICORN_WORKERS=1`, `UV_THREADPOOL_SIZE=2` and `NODE_OPTIONS=--max-old-space-size=256`
- Keep `RMS_DRY_RUN=true`, `RMS_LIVE_WRITE_ENABLED=false`, `RMS_PUSH_ENABLED=false` and `RMS_CREATE_PR=false` until a deliberate gated live-write test
- Keep `RMS_ALLOW_UNAUTHENTICATED_DEV=false` in Koyeb
- Set `RMS_API_KEY` as a Koyeb secret
- Store R2, GitHub and OpenRouter credentials as Koyeb secrets; do not paste secret values into repo files
- Enable shallow bootstrap with `RMS_GIT_CLONE_DEPTH=1` when repositories are not mounted
- Bound Git clone/fetch/status diagnostics with `RMS_GIT_OUTPUT_MAX_BYTES=65536`; Git and validation share the process-tree-safe runner
- Keep at least `RMS_MIN_FREE_DISK_MB=256`; RAMS rejects new runs below the threshold
- Local report and failed-clone cleanup is bounded to RAMS-owned temporary paths
- Do not add `npm install` or `npm ci` to runtime validation on eco-micro. The target repositories must arrive with what their configured validation requires, or heavyweight full validation should remain a GitHub Actions merge gate

### OpenRouter roles

The environment template uses three explicit roles rather than one expensive model everywhere:

| Role | Recommended model | Purpose |
|---|---|---|
| Primary | `qwen/qwen3.7-plus` | Cost-effective repository reasoning and bounded patch planning |
| Secondary | `openai/gpt-5.4-mini` | Independent fallback after a retryable primary failure |
| Triage | `openai/gpt-5.4-nano` | Classification and compact routing decisions |

Provider routing defaults to `price`, prompts are not logged, usage/cost metadata is aggregated into run reports, and repository evidence is restricted to providers that satisfy `RMS_OPENROUTER_DATA_COLLECTION=deny`. Model availability and prices can change, so review the IDs before altering production environment values.

### Memory, disk and shutdown behaviour

- Audit evidence, source context, repository indexing, validation output and report serialisation are all bounded by environment settings.
- Blocking R2, Git and validation work is moved off the FastAPI event loop while the effective heavy-work concurrency remains one.
- Validation commands stay sequential and their process groups are terminated on timeout or shutdown.
- SIGTERM stops new admissions, closes reusable HTTP clients and uses a shutdown grace below Koyeb's platform limit.
- `/health` never contacts R2, Git, Node, npm or OpenRouter, so it remains cheap during a run.

Do not treat a green `/health` as deployment readiness. It proves the process is alive; `/readiness` reports whether the grown-up machinery has its boots on. 🛠️

### References

- Koyeb instance reference: https://www.koyeb.com/docs/reference/instances
- OpenRouter provider routing: https://openrouter.ai/docs/guides/routing/provider-selection
- OpenRouter usage accounting: https://openrouter.ai/docs/cookbook/administration/usage-accounting
- Qwen3.7 Plus: https://openrouter.ai/qwen/qwen3.7-plus
- GPT-5.4 Mini: https://openrouter.ai/openai/gpt-5.4-mini
- GPT-5.4 Nano: https://openrouter.ai/openai/gpt-5.4-nano

## Provenance

See [`CLEAN_ROOM_PROVENANCE.md`](CLEAN_ROOM_PROVENANCE.md). RAMS is built from the RMS specification only. The separate reference archive is excluded from source, Docker context, CI, and release artefacts.
