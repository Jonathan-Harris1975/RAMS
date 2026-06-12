# RAMS eco-micro test results

**Run date:** 12 June 2026  
**Execution interpreter:** Python 3.13.5  
**Project support target:** Python 3.11+  
**Network/model calls during tests:** none

## Final gates

| Gate | Command | Result |
|---|---|---|
| Compile | `python -m compileall -q repo_mgmt tests scripts/emicro_benchmark.py` | Passed |
| Unit/integration tests | `python -m pytest -q` | **251 passed** |
| Lint | `python -m ruff check .` | Passed |
| Strict typing | `python -m mypy repo_mgmt/ --show-error-codes` | Passed, 32 source files |
| Shell syntax | `bash -n scripts/release_gate.sh scripts/smoke_test.sh` | Passed |
| Git safety drill | `python scripts/disposable_live_branch_check.py` | Passed |
| Benchmark | `python scripts/emicro_benchmark.py` | Passed, no external calls |
| Docker build/smoke | `docker build ...` | Not run: Docker executable unavailable in this environment |

## Pytest result

```text
251 passed, 2 warnings in 13.12s
```

The two warnings are deprecation notices emitted by the installed third-party `pathspec` package for its legacy `gitwildmatch` factory. They are not RAMS test failures and do not affect the current supported API.

## New or expanded coverage

- Cross-pipeline API rejection.
- Cross-pipeline direct execution lock.
- Idempotent MAST replay.
- Warm-up endpoint performs no external or operational work.
- Reusable synchronous/asynchronous OpenRouter clients.
- Primary-to-secondary fallback.
- Non-retryable error handling.
- JSON-mode compatibility recovery.
- Provider routing payload.
- Token caps.
- Nested cached/reasoning token accounting.
- Provider and cost accounting.
- Context file-count, byte, binary and traversal boundaries.
- Audit artefact count budget.
- Repository heavy-directory pruning and maximum file count.
- Validation output byte/line caps.
- Validation timeout and process termination.
- Git/bootstrap output uses the shared byte/line-bounded process runner.
- R2 oversize rejection closes streaming bodies on success and failure.

## Benchmark data

### Baseline

```json
{
  "context": {
    "bytesLoaded": 983040,
    "durationMs": 43.996,
    "filesLoaded": 60,
    "fixtureFiles": 60,
    "tracemallocPeakBytes": 1009855
  },
  "externalCalls": 0,
  "health": {
    "meanMs": 1.254,
    "p50Ms": 1.131,
    "p95Ms": 1.555,
    "requests": 100
  },
  "importMs": 1008.536,
  "label": "baseline",
  "maxRssKb": 351576,
  "moduleRoot": "/mnt/data/rams_baseline/RAMS-main",
  "python": "3.13.5",
  "repositoryIndex": {
    "durationMs": 205.287,
    "fixtureFiles": 760,
    "indexedFiles": 760,
    "tracemallocPeakBytes": 156975,
    "truncated": false
  },
  "validation": {
    "durationMs": 2133.255,
    "passed": true,
    "retainedOutputBytes": 200199,
    "tracemallocPeakBytes": 1504550
  }
}
```

### Optimised

```json
{
  "context": {
    "bytesLoaded": 131072,
    "durationMs": 6.184,
    "filesLoaded": 8,
    "fixtureFiles": 60,
    "tracemallocPeakBytes": 161051
  },
  "externalCalls": 0,
  "health": {
    "meanMs": 1.091,
    "p50Ms": 1.091,
    "p95Ms": 1.285,
    "requests": 100
  },
  "importMs": 1013.332,
  "label": "optimised",
  "maxRssKb": 336212,
  "moduleRoot": "/mnt/data/rams_work/RAMS-main",
  "python": "3.13.5",
  "repositoryIndex": {
    "durationMs": 29.904,
    "fixtureFiles": 760,
    "indexedFiles": 260,
    "tracemallocPeakBytes": 82135,
    "truncated": false
  },
  "validation": {
    "durationMs": 1929.418,
    "passed": true,
    "retainedOutputBytes": 16045,
    "tracemallocPeakBytes": 87117
  }
}
```

### Interpretation

- Repository indexing is faster because generated/heavy directories are pruned before file enumeration.
- Context collection is faster and uses substantially less traced memory because total files and bytes are bounded.
- Validation retains only a small diagnostic tail rather than the whole subprocess output.
- `/health` remains around one millisecond in the local TestClient benchmark and performs no external checks.
- Process RSS includes the complete Python test stack and should not be used as a direct Koyeb idle-memory prediction.

## Tests not performed

- Live OpenRouter model quality/cost call.
- Live Cloudflare R2 read/write.
- Live private GitHub clone/push/PR.
- Docker image size and container RSS.
- Actual target-repository `npm test && npm run build` on Koyeb eco-micro.

These require production credentials, billable services, Docker or the deployed environment and were not guessed.
