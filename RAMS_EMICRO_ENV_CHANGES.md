> **Document status:** Production reference  
> **Last reviewed:** 16 June 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

# RAMS eco-micro environment changes

**Target:** Koyeb `eco-micro`, 0.25 vCPU, 512MB RAM, 4GB ephemeral SSD  
**Important:** A production RAMS environment export was not attached. “Current” below refers to the supplied repository templates where known; deployed values and all secrets require Koyeb confirmation.

## Recommended OpenRouter roles

| Variable | Classification | Current template | Recommended | Reason | Impact | Code required |
|---|---|---|---|---|---|---|
| `OPENROUTER_PRIMARY_MODEL` | CHANGE | blank/example | `qwen/qwen3.7-plus` | Cost-effective current repository/coding model | Lower model cost; quality must be dry-run checked | Yes, role already supported |
| `OPENROUTER_SECONDARY_MODEL` | CHANGE | blank/example | `openai/gpt-5.4-mini` | Independent capable fallback | Cost only on transient primary failure | Yes, existing fallback enhanced |
| `OPENROUTER_TRIAGE_MODEL` | CHANGE | blank/example | `openai/gpt-5.4-nano` | Cheap classification/routing | Low latency and cost | Yes, existing triage enhanced |
| `OPENROUTER_API_BASE` | KEEP | official endpoint | `https://openrouter.ai/api/v1` | OpenAI-compatible endpoint | Neutral | No |
| `OPENROUTER_API_KEY` | NEEDS CONFIRMATION | masked/unknown | Koyeb secret | Required credential | Cost/security | No |
| `OPENROUTER_HTTP_REFERER` | ADD | absent | `https://jonathan-harris.online` | App attribution | Neutral | Yes |
| `OPENROUTER_APP_NAME` | ADD | absent | `RAMS` | Usage identification | Neutral | Yes |

## Runtime and process baseline

| Variable | Classification | Recommended | Memory | CPU/speed | Disk | Risk |
|---|---|---:|---|---|---|---|
| `PYTHONUNBUFFERED` | ADD | `1` | Neutral | Faster log visibility | Neutral | Low |
| `PYTHONDONTWRITEBYTECODE` | ADD | `1` | Neutral | Tiny import trade-off | Avoids `.pyc` growth | Low |
| `PYTHONHASHSEED` | ADD | `random` | Neutral | Neutral | Neutral | Low |
| `MALLOC_ARENA_MAX` | ADD | `2` | Reduces allocator arena growth | Neutral | Neutral | Low |
| `WEB_CONCURRENCY` | ADD/CHANGE | `1` | Prevents duplicate process memory | Prevents quarter-vCPU contention | Neutral | Critical |
| `UVICORN_WORKERS` | ADD/CHANGE | `1` | Prevents duplicate process memory | Correct for 0.25 vCPU | Neutral | Critical |
| `UV_THREADPOOL_SIZE` | ADD | `2` | Limits thread overhead | Enough for bounded offloads | Neutral | Low |
| `NODE_OPTIONS` | ADD | `--max-old-space-size=256` | Caps Node heap | May fail an oversized build instead of killing service | Neutral | Medium |
| `LOG_LEVEL` | KEEP | `info` | Avoids debug-log accumulation | Lower logging overhead | Lower log volume | Low |

## OpenRouter transport, routing and generation

| Variable | Classification | Recommended | Reason / effect | Risk |
|---|---|---:|---|---|
| `RMS_OPENROUTER_CONNECT_TIMEOUT_SECONDS` | ADD | `5` | Fail quickly on connection failure | Low |
| `RMS_OPENROUTER_READ_TIMEOUT_SECONDS` | ADD | `90` | Allows structured patch output without indefinite waits | Medium |
| `RMS_OPENROUTER_WRITE_TIMEOUT_SECONDS` | ADD | `30` | Bounds request upload | Low |
| `RMS_OPENROUTER_POOL_TIMEOUT_SECONDS` | ADD | `5` | Prevents pool starvation waits | Low |
| `RMS_OPENROUTER_MAX_CONNECTIONS` | ADD | `2` | Tiny reusable pool | Low |
| `RMS_OPENROUTER_MAX_KEEPALIVE_CONNECTIONS` | ADD | `1` | Reuses one connection without excess sockets | Low |
| `RMS_OPENROUTER_KEEPALIVE_EXPIRY_SECONDS` | ADD | `30` | Avoids stale long-lived connections | Low |
| `RMS_OPENROUTER_MAX_RETRIES` | ADD | `0` | One primary attempt then model fallback; avoids retry storms | Medium |
| `RMS_OPENROUTER_RETRY_BASE_SECONDS` | ADD | `1` | Used only if retries are deliberately enabled | Low |
| `RMS_OPENROUTER_RETRY_MAX_SECONDS` | ADD | `8` | Caps Retry-After/backoff inside model call | Low |
| `RMS_OPENROUTER_PROVIDER_SORT` | ADD | `price` | Lowest-price eligible provider | Low |
| `RMS_OPENROUTER_ALLOW_FALLBACKS` | ADD | `true` | Allows provider-level resilience | Low |
| `RMS_OPENROUTER_DATA_COLLECTION` | ADD | `deny` | Excludes providers that may collect repository data | May reduce model availability |
| `RMS_PRIMARY_MAX_TOKENS` | ADD | `3072` | Avoids default 4096-token over-generation | Medium |
| `RMS_SECONDARY_MAX_TOKENS` | ADD | `3072` | Same bounded patch ceiling | Medium |
| `RMS_TRIAGE_MAX_TOKENS` | ADD | `128` | Compact classification only | Low |
| `RMS_PRIMARY_TEMPERATURE` | ADD | `0` | Deterministic patch JSON | Low |
| `RMS_TRIAGE_TEMPERATURE` | ADD | `0` | Deterministic classification | Low |
| `RMS_TOP_P` | ADD | `0.9` | Conservative generation control | Low |
| `RMS_OPENROUTER_LOG_USAGE` | ADD | `true` | Records token/duration metadata | Low |
| `RMS_OPENROUTER_LOG_COST` | ADD | `true` | Records returned cost | Low |
| `RMS_OPENROUTER_LOG_PROMPTS` | ADD | `false` | Prevents source/prompt leakage | Critical |

## Admission, memory and disk ceilings

| Variable | Classification | Recommended | Primary impact | Risk |
|---|---|---:|---|---|
| `RMS_MAX_CONCURRENT_PIPELINES` | ADD | `1` | Prevents all cross-pipeline contention | Critical |
| `RMS_MAX_ISSUES_PER_RUN` | CHANGE | `1` | One patch/validation workload per run | Medium |
| `RMS_MAX_AUDIT_ARTEFACTS` | ADD | `8` | Bounds R2 requests/evidence objects | Low |
| `RMS_MAX_AUDIT_OBJECT_BYTES` | ADD | `1048576` | 1MB per evidence object | Medium |
| `RMS_MAX_AUDIT_TOTAL_BYTES` | ADD | `4194304` | 4MB total evidence | Medium |
| `RMS_MAX_CONTEXT_FILES` | ADD | `8` | Bounds source files in prompt | Medium |
| `RMS_MAX_CONTEXT_FILE_BYTES` | ADD | `131072` | 128KB per source file | Medium |
| `RMS_MAX_CONTEXT_TOTAL_BYTES` | ADD | `524288` | 512KB total source context | Medium |
| `RMS_MAX_INDEXED_FILES` | ADD | `20000` | Bounds repository metadata | Low |
| `RMS_REPORT_MAX_BYTES` | ADD | `4194304` | Prevents oversized report serialisation | Medium |
| `RMS_MIN_FREE_DISK_MB` | ADD | `256` | Refuses work before disk exhaustion | Low |
| `RMS_TEMP_CLEANUP_ENABLED` | ADD | `true` | Deletes stale RAMS-owned reports | Low |
| `RMS_TEMP_MAX_AGE_HOURS` | ADD | `24` | Defines stale-report age | Low |
| `RMS_READINESS_CACHE_SECONDS` | ADD | `300` | Avoids repeated runtime subprocesses | Low |
| `RMS_IDEMPOTENCY_CACHE_SIZE` | ADD | `128` | Bounds MAST run-key memory | Low |
| `RMS_BUSY_RETRY_AFTER_SECONDS` | ADD | `60` | Gives schedulers a retry hint | Low |
| `RMS_SHUTDOWN_GRACE_SECONDS` | ADD | `25` | Fits platform shutdown window | Medium |

## Git, validation and repository paths

| Variable | Classification | Recommended | Reason | Risk |
|---|---|---|---|---|
| `RMS_REPO_BOOTSTRAP_ENABLED` | NEEDS CONFIRMATION | `true` on Koyeb when repos are not mounted | Required for ephemeral container paths | Medium |
| `RMS_REPO_BASE_DIR` | KEEP/CHANGE | `/tmp/rams-repos` | RAMS-owned ephemeral area | Low |
| `RMS_WEBSITE_REPO_PATH` | NEEDS CONFIRMATION | `/tmp/rams-repos/website` | Website target | Medium |
| `RMS_AIMS_REPO_PATH` | NEEDS CONFIRMATION | `/tmp/rams-repos/aims` | AIMS target | Medium |
| `RMS_WEBSITE_REPO_URL` | NEEDS CONFIRMATION | actual Git URL | Bootstrap source | Medium |
| `RMS_AIMS_REPO_URL` | NEEDS CONFIRMATION | actual Git URL | Bootstrap source | Medium |
| `RMS_WEBSITE_REPO_BRANCH` | KEEP | `main` | Expected source branch | Low |
| `RMS_AIMS_REPO_BRANCH` | KEEP | `main` | Expected source branch | Low |
| `RMS_GITHUB_TOKEN` | NEEDS CONFIRMATION | Koyeb secret | Private clone/write credential | Critical |
| `GITHUB_TOKEN` | KEEP AS ALIAS | blank unless required | Backwards compatibility | Low |
| `RMS_GIT_CLONE_DEPTH` | ADD | `1` | Minimum clone disk/time | Low |
| `RMS_GIT_TIMEOUT_SECONDS` | ADD | `120` | Bounds clone/fetch | Medium |
| `RMS_GIT_OUTPUT_MAX_BYTES` | ADD | `65536` | Caps retained clone/fetch/status diagnostics | Low |
| `RMS_WEBSITE_VALIDATION_COMMANDS` | NEEDS CONFIRMATION | existing three Python checks | Verify commands exist in current website repo | Medium |
| `RMS_AIMS_VALIDATION_COMMANDS` | NEEDS CONFIRMATION | `npm test && npm run build` | May be too heavy for 512MB; do not add npm install/ci | High |
| `RMS_VALIDATION_TIMEOUT_SECONDS` | ADD | `240` | Bounds each sequential command | Medium |
| `RMS_VALIDATION_OUTPUT_MAX_LINES` | ADD | `120` | Small diagnostic tail | Low |
| `RMS_VALIDATION_OUTPUT_MAX_BYTES` | ADD | `131072` | 128KB maximum retained output | Low |

## Existing safety and API variables

| Variable | Classification | Recommended | Reason |
|---|---|---:|---|
| `RMS_DRY_RUN` | KEEP | `true` initially | Fail-safe deployment |
| `RMS_LIVE_WRITE_ENABLED` | KEEP | `false` initially | Independent live-write gate |
| `RMS_PUSH_ENABLED` | KEEP | `false` initially | No automatic remote writes |
| `RMS_CREATE_PR` | KEEP | `false` initially | No automatic PR creation |
| `RMS_VALIDATE_AFTER_EACH_TASK` | KEEP | `true` | Preserve safety validation |
| `RMS_REVERT_ON_VALIDATION_FAILURE` | KEEP | `true` | Preserve rollback |
| `RMS_SINGLE_WORKER_MODE` | KEEP | `true` | Enforce deployment model |
| `RMS_REPORT_PREFIX` | KEEP | `qa-suite/reports` | Existing report layout |
| `RMS_REPORT_DIR` | KEEP | `/tmp/rams-reports` | Ephemeral local fallback/report path |
| `RMS_QA_BRANCH_PREFIX` | KEEP | `rms-qa/` | Existing branch safety |
| `RMS_API_KEY` | NEEDS CONFIRMATION | strong Koyeb secret | Protect readiness, warm-up, reports and rebuilds |
| `RMS_ALLOW_UNAUTHENTICATED_DEV` | KEEP | `false` | Fail closed in production |
| `RMS_HOST` | KEEP | `0.0.0.0` | Required container bind |
| `RMS_PORT` | KEEP | `8000` or Koyeb `PORT` | Service port |

## Remove

No production variable is recommended for removal without the actual Koyeb export. `RMS_SEO_REPO_PATH`, `RMS_SEO_VALIDATION_COMMANDS` and `GITHUB_TOKEN` remain compatibility aliases in code and should only be removed in a later breaking cleanup after confirming no deployment or automation still uses them.

## Deployment order

1. Apply non-secret runtime/resource values.
2. Confirm repo URLs, paths and validation commands.
3. Add secrets through Koyeb Secrets.
4. Deploy with all live-write controls false.
5. Verify `/health`, authenticated `/ops/warmup`, then `/readiness`.
6. Run one dry-run pipeline.
7. Inspect report AI usage, validation tail, disk guard and runtime logs.
8. Keep live writes disabled until the actual AIMS build proves it fits eco-micro.
