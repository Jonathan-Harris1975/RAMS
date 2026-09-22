#!/usr/bin/env bash
set -euo pipefail

python -m pip install --disable-pip-version-check --require-hashes -r requirements-build.txt
python -m pip install --disable-pip-version-check --require-hashes -r requirements-runtime.lock
python -m pip install --disable-pip-version-check --no-index --no-deps --no-build-isolation .
python -m pip check
