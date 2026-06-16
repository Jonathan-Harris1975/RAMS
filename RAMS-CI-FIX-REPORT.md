# RAMS CI Fix Report

## Failure summary

The uploaded GitHub Actions run had two independent failures:

1. `mypy` rejected `repo_mgmt/api.py` because the HTTP middleware lacked parameter and return type annotations.
2. The Docker API smoke test correctly received HTTP 503 from `/readyz` for deliberately degraded fake R2 credentials, but `curl -f` treated that expected status as a command failure and left the readiness JSON file empty.

The Python 3.11 and Python 3.12 test matrices passed with 253 tests.

## Changes

### `repo_mgmt/api.py`

- Added `RequestResponseEndpoint` and `Response` imports.
- Added full middleware parameter and return annotations.

### `.github/workflows/ci.yml`

- Updated `actions/checkout` to v6.
- Updated `actions/setup-python` to v6.
- Made the API startup loop fail explicitly if the service never becomes healthy.
- Captured the `/readyz` response body without treating the expected 503 status as a transport failure.
- Asserted that the degraded readiness probe returns HTTP 503 before validating the JSON body.

## Verification

- Ruff: passed.
- Mypy: passed for all 32 source files.
- Local API smoke test: passed.
- Expected degraded readiness status: HTTP 503.
- Existing test logs: 253 tests passed on Python 3.11 and Python 3.12.

## Deployment

Replace the two matching files in the RAMS repository, commit, and rerun GitHub Actions.
