# RAMS Release Evidence · 2026-05-14

## Summary

RAMS has been remediated to close the critical dry-run staging blockers from the supplied production-readiness report while keeping live writes, push, and PR automation gated.

Target state after this remediation:

| Area | Verdict |
|---|---|
| Dry-run staging | GO once real R2/OpenRouter values and real target repo paths are configured, and `/readiness` reports `ready` |
| Disposable live branch tests | GO for controlled disposable clones after dry-run classifications are reviewed |
| Live production writes | NO-GO until repeated disposable live runs prove rollback, validation, and exact-path staging |
| Push / PR automation | NO-GO; push remains gated and PR creation is not implemented |

## Files changed

- `.github/workflows/ci.yml`
- `Dockerfile`
- `README.md`
- `RELEASE_GATE.md`
- `RELEASE_EVIDENCE_2026-05-14.md`
- `pyproject.toml`
- `repo_mgmt/api.py`
- `repo_mgmt/pipeline.py`
- `repo_mgmt/r2_client.py`
- `repo_mgmt/report_publisher.py`
- `repo_mgmt/schemas.py`
- `scripts/disposable_live_branch_check.py`
- `scripts/release_gate.sh`
- `tests/conftest.py`
- `tests/test_api.py`

## Blocker-by-blocker remediation

| Finding | File(s) changed | Fix applied | Evidence | Status |
|---|---|---|---|---|
| Runtime image lacked Node/npm | `Dockerfile`, `.github/workflows/ci.yml`, `README.md`, `RELEASE_GATE.md` | Runtime now copies Node.js 20 from the official Node image and includes npm; Dockerfile smoke-checks Python, Git, Node, and npm during build. CI runs container binary checks. | Docker unavailable in this assessment container, so Docker build/run is delegated to CI and `scripts/release_gate.sh`. | Fixed in source; Docker execution not locally runnable |
| Release gates incomplete | `.github/workflows/ci.yml`, `scripts/release_gate.sh`, `RELEASE_GATE.md` | Added full release-gate script and CI checks for compile, pytest, ruff, mypy, Docker build, Docker boot, `/health`, `/readiness`, and runtime binaries. | Local compile, pytest, ruff passed. Docker unavailable locally. Mypy did not complete in this constrained container. | Fixed in repo; CI must execute Docker/mypy gate |
| R2 readiness only proved client construction | `repo_mgmt/r2_client.py`, `repo_mgmt/api.py`, `tests/test_api.py` | Added `verify_bucket()` using a safe `HeadBucket` probe with short timeouts. `/readiness` now separates `r2_configured` and `r2_verified`; pipeline admission refuses unverified R2. | Local API with fake R2 reports `r2_configured=true`, `r2_verified=false`, and `status=degraded`. | Fixed |
| `/health` could mask dependency failure | `repo_mgmt/api.py`, `README.md`, `RELEASE_GATE.md` | Kept `/health` as liveness-only with the exact RMS shape. `/readiness` carries dependency state. Docs and gates now require `/readiness` before triggers. | Local `/health` returned only status plus pipeline states while `/readiness` degraded on fake R2. | Fixed |
| Live autonomous writes not evidenced | `scripts/disposable_live_branch_check.py`, `RELEASE_GATE.md`, existing Git/update tests | Added a disposable local safety drill for QA branch creation, refusal to write on `main`, exact-path staging, and task-scoped rollback. Existing tests cover validation rollback, branch safety, exact staging, and dirty worktree refusal. | `python scripts/disposable_live_branch_check.py` passed. Real target repo live tests remain intentionally not run here. | Converted to gated release procedure |
| RunReport validation could be `null` | `repo_mgmt/pipeline.py`, `repo_mgmt/report_publisher.py`, `repo_mgmt/schemas.py` | Run reports now always emit a validation object with `commands`, `passed`, and `outputTail`. Not-run states use explicit not-run metadata in `outputTail`. | Pytest suite passed with schema enforcement. | Fixed |
| Dry-run report path drift | `README.md`, `RELEASE_GATE.md` | Documented `RMS_REPORT_DIR` as an accepted production deviation from the original prompt because it is safer for containers/Koyeb. | Docs updated. | Accepted deviation |
| Scheduler/spec drift | `README.md`, `RELEASE_GATE.md` | Documented external scheduling as the intended Koyeb-friendly model. No in-process scheduler added. | Docs updated. | Accepted deviation |
| PR creation not implemented | `README.md`, `RELEASE_GATE.md` | Kept `RMS_CREATE_PR=false` as the required state. Docs state PR creation is not implemented. | Docs updated; safety gate remains closed. | Accepted gated state |
| Lazy config validation | `README.md`, `RELEASE_GATE.md`, existing API design | Kept lazy load so Koyeb liveness can boot. `/readiness` remains strict and trigger admission refuses missing dependencies. | Missing/fake dependencies produce degraded readiness / 503 trigger refusal. | Accepted operational deviation |
| Clean-room risk | source scan | Ran lexical scan for forbidden reference-project tokens across remediated RAMS. | Scan produced no matches. | Passed |

## Commands run locally

```bash
python -V
# Python 3.13.5

python -m compileall -q repo_mgmt tests
# PASS

python -m pytest tests/ -q --tb=short
# 181 passed, 2 warnings

python -m ruff check .
# All checks passed

python scripts/disposable_live_branch_check.py
# Disposable live-branch safety check passed.
```

API boot smoke with fake R2:

```bash
python -m uvicorn repo_mgmt.api:app --host 127.0.0.1 --port 8766
curl -sS http://127.0.0.1:8766/health
# {"status":"ok","pipelines":{"seo-aeo-geo":"idle","mobile-ux":"idle","on-brand":"idle"}}

curl -sS http://127.0.0.1:8766/readiness
# status=degraded, r2_configured=true, r2_verified=false,
# seo_repo_ready=true, website_repo_ready=true, validation_runtime_ready=true
```

Clean-room lexical scan:

```bash
grep -RIn -E '<forbidden-token-regex>' .
# PASS: no matches
```

## Commands not run locally

| Command | Reason | Repo-level gate added |
|---|---|---|
| `python -m mypy repo_mgmt/ --no-incremental --show-error-codes` | Timed out in the constrained assessment container, matching the earlier audit behaviour. | CI lint job and `scripts/release_gate.sh` run mypy on a clean Linux runner. |
| `docker build -t rams-production-check .` | Docker is not installed in this environment. | CI Docker job and `scripts/release_gate.sh` run Docker build. |
| `docker run --rm rams-production-check ...` | Docker is not installed in this environment. | CI Docker job verifies Python, Git, Node, npm, `/health`, and `/readiness`. |
| Real R2 readiness with production credentials | Real credentials were not supplied in the assessment environment. | `/readiness` performs the live bucket probe when real env is present. |
| Real disposable target-repo live write run | Real target repos were not supplied/mounted as live disposable clones. | `scripts/disposable_live_branch_check.py` plus Gate 7 defines the controlled procedure. |

## Koyeb deployment notes

- Use one web service, one instance, one worker.
- Startup command: `rms-api`.
- Liveness probe: `/health`.
- Operator/pre-trigger readiness: `/readiness`.
- Keep `RMS_DRY_RUN=true` for staging.
- Keep `RMS_LIVE_WRITE_ENABLED=false`, `RMS_PUSH_ENABLED=false`, and `RMS_CREATE_PR=false`.
- Ensure the container has real target repo paths available at `RMS_SEO_REPO_PATH` and `RMS_WEBSITE_REPO_PATH`.
- Use real R2 credentials; fake endpoints must not pass readiness.
- The service honours `RMS_PORT`, then `PORT`, then `8000`.

## Final verdict

| Capability | Verdict |
|---|---|
| Dry-run staging | GO after Docker/CI gate and real `/readiness=ready` in Koyeb |
| Disposable live branch tests | GO on disposable clones only after dry-run review |
| Live production writes | NO-GO |
| Push automation | NO-GO |
| PR automation | NO-GO |
