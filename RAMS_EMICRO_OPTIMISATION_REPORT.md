> **Document status:** Historical implementation record  
> **Last reviewed:** 21 June 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

# RAMS Koyeb eco-micro optimisation report

**Completed:** 12 June 2026  
**Target:** Koyeb `eco-micro`, 0.25 vCPU, 512MB RAM, 4GB ephemeral SSD  
**Primary repository:** `RAMS-main.zip`  
**Reference repository:** `MAST-main.zip`  
**Deployment environment note:** This file is a historical optimisation report. For current production deployment values and secret bindings, use `RAMS-KOYEB-PRODUCTION-ENV.txt` and `docs/PRODUCTION_DEPLOYMENT_CHECKLIST.md`.

## Executive summary

RAMS has been changed from three independently lockable heavyweight pipelines into a globally single-admission service suitable for a quarter-vCPU instance. The API, CLI/direct pipeline layer, OpenRouter transport, R2 evidence loading, source-context loading, validation output, Git bootstrap, report serialisation and temporary-file handling are now bounded.

The optimisation is environment-first. The code adds validated `RMS_*` controls, while `.env.template`, `.env.example-dry-run` and Docker defaults provide the conservative eco-micro baseline. Existing dry-run, protected-path, branch, rollback, validation and live-write gates remain fail-closed.

AIMS `ops` files were correctly treated as warm-up infrastructure rather than ordinary task processors. RAMS now exposes an authenticated `/ops/warmup` endpoint with the same operating principle: it warms configuration and bounded HTTP clients only. It does not access repositories, R2, audits, validation or OpenRouter.

## Principal findings

### 1. Cross-pipeline overload risk

The original locking was per pipeline. `mobile-ux`, `seo-aeo-geo` and `on-brand` could therefore run simultaneously. On eco-micro that could combine repository traversal, R2 reads, model requests, Git activity and Node/Python validation inside 512MB RAM.

**Resolution:**

- Global API admission allows one RAMS pipeline at a time.
- A second request for the same pipeline returns `409`.
- A different pipeline while RAMS is active returns `429` with `Retry-After`.
- A second global lock exists inside `RmsPipeline`, preventing CLI or direct calls from bypassing API admission.
- `RMS_MAX_CONCURRENT_PIPELINES` is validated as exactly `1`.

### 2. Duplicate MAST retry risk

MAST already sends both `X-Trigger-Run-Key` and `X-Idempotency-Key`, but RAMS previously did not use them.

**Resolution:**

- RAMS accepts either header.
- A bounded process-local cache replays the original `202` response and run ID.
- The cache cannot grow without limit.
- The existing staggered MAST rebuild/report timetable remains compatible.
- No MAST source modification was required.

### 3. OpenRouter connection and cost overhead

The previous implementation could construct one-shot clients, used broad generation caps and did not aggregate complete returned usage data.

**Resolution:**

- One reusable synchronous and one reusable asynchronous `httpx` client.
- Maximum two connections and one keep-alive connection by default.
- Separate connect/read/write/pool timeouts.
- Zero same-model retries by default, followed by one independent secondary-model fallback only for approved transient failures.
- `Retry-After` and bounded exponential backoff are supported when retries are enabled.
- Primary, secondary and triage token caps are separate.
- Low deterministic temperatures are environment-controlled.
- Provider routing, provider fallbacks and data-collection policy are environment-controlled.
- Prompt logging remains disabled.
- Run reports aggregate model, provider, prompt, completion, reasoning, cached-token, duration and cost data returned by OpenRouter.

### 4. Unbounded evidence and source context

Audit manifests could dereference many JSON artefacts and context collection had only a per-file size check.

**Resolution:**

- Audit artefact count, object bytes and total evidence bytes are bounded.
- Source-context file count, per-file bytes and total bytes are bounded.
- Duplicate paths are removed.
- Binary and oversized files are rejected.
- Path traversal protection remains intact.
- Repository indexing prunes `node_modules`, build output, caches, virtual environments and hidden directories even when `.gitignore` is incomplete.

### 5. Validation memory and timeout risk

Subprocess output was captured without a strict retained-output ceiling.

**Resolution:**

- Validation commands remain sequential.
- Output retention is capped by bytes and lines.
- Commands have an environment-controlled timeout.
- Each command starts in its own process group.
- Timeout/shutdown terminates the process tree.
- The operator-configured `&&` command chain remains under `shell=True`; audit/model content is never interpolated into it.
- Blocking validation and Git/R2 operations are moved away from the FastAPI event loop without increasing heavy-work concurrency.

### 6. Ephemeral disk growth

The target has only 4GB ephemeral disk.

**Resolution:**

- Repository clones are shallow, single-branch and no-tags.
- Failed partial clones are removed.
- New admissions are rejected below `RMS_MIN_FREE_DISK_MB`.
- RAMS-owned stale local reports are cleaned at startup.
- Report payload size is bounded.
- Temporary cleanup is constrained to configured RAMS paths.
- Python bytecode and package caches are disabled in the production baseline.

### 7. Health, readiness and shutdown

A small service needs liveness to remain cheap while background work runs.

**Resolution:**

- `/health` performs no R2, Git, Node, npm, repository or model work.
- `/readiness` caches runtime version checks.
- `/ops/warmup` performs no outbound requests.
- FastAPI lifespan replaces deprecated startup/shutdown hooks.
- Shutdown stops new admission and closes reusable clients.
- Uvicorn is explicitly limited to one worker with a 25-second graceful-shutdown setting.

## OpenRouter recommendation

Model availability and pricing were checked on 12 June 2026 against official OpenRouter model pages.

| Role | Model ID | Input / output price per 1M tokens | Context | Rationale |
|---|---|---:|---:|---|
| Primary | `qwen/qwen3.7-plus` | $0.32 / $1.28 | 1M | Current cost-effective Qwen agent model with coding, tool-use and productivity capability. Suitable for repository-level patch planning at substantially lower output cost than the fallback. |
| Secondary | `openai/gpt-5.4-mini` | $0.75 / $4.50 | 400K | Independent fallback with strong coding, reasoning and instruction-following capability. Used only after an approved transient primary failure. |
| Triage | `openai/gpt-5.4-nano` | $0.20 / $1.25 | 400K | Lightweight model designed for classification, extraction, ranking and background/sub-agent work. |

Official references:

- https://openrouter.ai/qwen/qwen3.7-plus
- https://openrouter.ai/openai/gpt-5.4-mini
- https://openrouter.ai/openai/gpt-5.4-nano
- https://openrouter.ai/docs/guides/routing/provider-selection
- https://openrouter.ai/docs/cookbook/administration/usage-accounting

### Model caveat

`qwen/qwen3.7-plus` was listed with one provider at review time. RAMS defaults to `RMS_OPENROUTER_DATA_COLLECTION=deny`. The actual production API key/privacy policy was not available for a live compatibility call. Before enabling live writes, run a dry-run model request. If OpenRouter cannot serve Qwen under the deny policy, keep the privacy policy and set the primary to `openai/gpt-5.4-mini`; do not weaken repository privacy merely to preserve the cheaper model.

## Benchmark comparison

Secret-free synthetic fixture: 760 repository files, including 500 files in directories that should be excluded; 60 context files of 16KB each; 500 lines of large validation output. No R2, GitHub or OpenRouter call was made.

| Measure | Baseline | Optimised | Result |
|---|---:|---:|---:|
| Repository index duration | 205.287 ms | 29.904 ms | 85.4% lower |
| Files indexed | 760 | 260 | Heavy generated directories excluded |
| Index traced peak | 156,975 B | 82,135 B | 47.7% lower |
| Context duration | 43.996 ms | 6.184 ms | 85.9% lower |
| Context bytes retained | 983,040 B | 131,072 B | 86.7% lower |
| Context traced peak | 1,009,855 B | 161,051 B | 84.1% lower |
| Validation duration | 2,133.255 ms | 1,929.418 ms | 9.6% lower in this run |
| Validation output retained | 200,199 B | 16,045 B | 92.0% lower |
| Validation traced peak | 1,504,550 B | 87,117 B | 94.2% lower |
| `/health` p95 | 1.555 ms | 1.285 ms | Remained lightweight |

The process-level maximum RSS result includes the test framework, FastAPI, botocore and Python runtime and is too noisy to represent an idle Koyeb container. The bounded component measurements above are the defensible comparison.

Raw results are in `benchmark-results/baseline.json` and `benchmark-results/optimised.json`.

## Changed files

| Path | Purpose | Necessity | Risk | Coverage |
|---|---|---|---|---|
| `.env.template` | Complete eco-micro and OpenRouter baseline | Required | Low | Parsed during verification |
| `.env.example-dry-run` | Safe example aligned with new settings | Required | Low | Used by Docker release gate |
| `.github/workflows/ci.yml` | Current model IDs and warm-up smoke assertion | Required | Low | GitHub CI |
| `Dockerfile` | Single-process, memory and cache defaults | Required | Medium | Docker CI, not locally available |
| `README.md` | eco-micro, warm-up, idempotency and MAST contract | Required | Low | Documentation review |
| `RELEASE_GATE.md` | Reproducible release and deployment gates | Required | Low | Shell/command review |
| `repo_mgmt/api.py` | Global admission, idempotency, warm-up, lifespan, disk gate, cached readiness | Required | Medium | API tests |
| `repo_mgmt/audit_reader.py` | Bounded R2 evidence loading | Required | Medium | Audit-reader tests |
| `repo_mgmt/config.py` | Validated environment-first controls | Required | Medium | Config and full suite |
| `repo_mgmt/context_builder.py` | Total context/file/binary bounds | Required | Medium | New context tests |
| `repo_mgmt/git_manager.py` | Bounded Git status/branch/commit/push diagnostics | Required | Medium | Git safety tests |
| `repo_mgmt/model_router.py` | Reusable clients, routing, retries, usage/cost accounting | Required | Medium | 11 model-router tests |
| `repo_mgmt/patch_planner.py` | Use the bounded shared context loader | Required | Low | Planner/full suite |
| `repo_mgmt/pipeline.py` | Global execution lock, bounded evidence, AI usage report | Required | High | Pipeline/API tests |
| `repo_mgmt/process_runner.py` | Shared byte/line-bounded subprocess runner with process-group termination | Required | High | Validation and Git tests |
| `repo_mgmt/r2_client.py` | Size-limited R2 object read with guaranteed stream closure | Required | Medium | Dedicated R2/full suite |
| `repo_mgmt/repo_bootstrap.py` | Shallow/no-tag clone, bounded output and partial-clone cleanup | Required | Medium | Bootstrap/full suite |
| `repo_mgmt/repo_index.py` | Pruned, bounded repository walk | Required | Medium | Repo-index tests/benchmark |
| `repo_mgmt/report_publisher.py` | Report size ceiling and AI usage field | Required | Medium | Report/full suite |
| `repo_mgmt/runtime_guard.py` | Safe stale RAMS report cleanup | Required | Low | Startup/full suite |
| `repo_mgmt/update_executor.py` | Offload blocking validation/Git operations | Required | Medium | Executor/full suite |
| `repo_mgmt/validation_runner.py` | Uses shared bounded output and process-tree timeout runner | Required | High | Validation tests/benchmark |
| `scripts/emicro_benchmark.py` | Repeatable secret-free benchmark | Required | Low | Executed baseline/optimised |
| `scripts/release_gate.sh` | Warm-up smoke verification | Required | Low | Bash syntax; Docker unavailable |
| `scripts/smoke_test.sh` | Include authenticated warm-up probe | Required | Low | Bash syntax |
| `tests/test_api.py` | Global admission, idempotency and warm-up tests | Required | Low | Passed |
| `tests/test_audit_reader.py` | Artefact-budget test | Required | Low | Passed |
| `tests/test_context_builder.py` | New context-boundary suite | Required | Low | Passed |
| `tests/test_model_router.py` | Client, routing, fallback and usage tests | Required | Low | Passed |
| `tests/test_pipeline.py` | Cross-pipeline direct-call lock test | Required | Low | Passed |
| `tests/test_git_manager.py` | Bounded Git timeout/output tests | Required | Low | Passed |
| `tests/test_r2_client.py` | Oversize and stream-closure tests | Required | Low | Passed |
| `tests/test_repo_index.py` | Built-in pruning and file-limit tests | Required | Low | Passed |
| `tests/test_validation_runner.py` | Output and timeout tests | Required | Low | Passed |

## MAST compatibility

The supplied MAST reference was inspected but not modified.

Confirmed:

- MAST sends `X-Trigger-Run-Key` and `X-Idempotency-Key`.
- Monthly rebuilds are staggered: on-brand 04:30, mobile UX 06:40, SEO/AEO/GEO 07:40 in the configured local timezone.
- Corresponding reports are scheduled 30 minutes later.
- RAMS now understands MAST idempotency keys and returns a retryable busy contract.

Remaining MAST limitation:

- MAST's generic retry loop does not currently honour `Retry-After`; it uses its configured retry delay. Because the RAMS schedules are staggered and duplicate keys are replayed, this is not a blocking RAMS deployment issue. A future MAST refinement may use `Retry-After` for all services, but it was outside the minimum RAMS change set.

## Koyeb deployment instructions

1. Select `eco-micro`, one instance.
2. Build from the included Dockerfile.
3. Start with `rms-api`.
4. Set liveness to `/health`.
5. Copy non-secret values from `.env.template`.
6. Add R2, GitHub, OpenRouter and `RMS_API_KEY` through Koyeb Secrets.
7. Keep the initial deployment in dry-run mode.
8. Call `/ops/warmup`, then `/readiness`.
9. Run one manually triggered dry-run pipeline and inspect its R2/local report and AI usage.
10. For the paid production instance, live-write permission is governed by `RMS_DRY_RUN=false` and `RMS_LIVE_WRITE_ENABLED=true`; branch pushing and pull request creation remain separate controls and are intentionally disabled unless an operator enables them after clean evidence.

## Rollback

- Revert the changed files listed above to the supplied RAMS baseline.
- Restore previous Koyeb environment values.
- Keep `RMS_DRY_RUN=true` and `RMS_LIVE_WRITE_ENABLED=false` during rollback.
- Redeploy one instance and verify `/health` and `/readiness`.
- No persistent database migration or R2 schema migration was introduced.

## Remaining risks

- Docker was unavailable in the execution environment, so the image build and in-container Node/npm smoke gate were not run locally. GitHub CI contains the Docker gate.
- No production RAMS environment export was supplied, so actual Koyeb values and secret names were not reconciled.
- No live R2, GitHub or OpenRouter request was made. This was deliberate to avoid secret use and billable calls.
- Full `npm test && npm run build` against the actual AIMS clone may still exceed eco-micro limits depending on the target repo's dependency state. The code now bounds its output and duration, but cannot make an intrinsically large build fit 512MB. If it fails, retain lightweight local safety checks and make GitHub Actions the mandatory full merge gate.
- The idempotency cache is intentionally process-local. With one Koyeb instance this matches the deployment contract; it is not suitable for horizontal scaling.
