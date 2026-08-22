#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-rams-production-check}"
PORT="${PORT:-8000}"
RMS_RELEASE_GATE_API_KEY="${RMS_API_KEY:-example-local-rams-key}"

python -V
python -m compileall -q repo_mgmt tests
python -m pytest tests/ -q --tb=short
python -m ruff check .
python -m mypy repo_mgmt/ --no-incremental --show-error-codes

clean_room_pattern=$(printf '%s|' \
  "aid""er" \
  "Aid""er" \
  "edit""block" \
  "u""diff" \
  "whole""file" \
  "repo""map")
clean_room_pattern=${clean_room_pattern%|}
if grep -RIn -E "$clean_room_pattern" repo_mgmt tests README.md RELEASE_GATE.md pyproject.toml Dockerfile; then
  printf '\nForbidden clean-room term found. See output above.\n' >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  printf 'Docker is not available. Run this script in a Linux runner with Docker enabled.\n' >&2
  exit 2
fi

docker build --target runtime -t "$IMAGE_NAME" .
docker run --rm "$IMAGE_NAME" python --version
docker run --rm "$IMAGE_NAME" git --version
docker run --rm "$IMAGE_NAME" node --version
docker run --rm "$IMAGE_NAME" npm --version
docker run --rm "$IMAGE_NAME" node -e "process.exit(Number(process.versions.node.split('.')[0]) === 22 ? 0 : 1)"

docker rm -f rams-release-gate >/dev/null 2>&1 || true
docker run -d --name rams-release-gate -p "$PORT:8000" --env-file .env.example-dry-run -e RMS_API_KEY="$RMS_RELEASE_GATE_API_KEY" "$IMAGE_NAME" >/dev/null
cleanup() { docker logs rams-release-gate || true; docker rm -f rams-release-gate >/dev/null 2>&1 || true; }
trap cleanup EXIT

for _ in {1..30}; do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/tmp/rams-health.json; then
    break
  fi
  sleep 1
done

curl -fsS "http://127.0.0.1:${PORT}/health"
printf '\n'
curl -fsS -H "Authorization: Bearer ${RMS_RELEASE_GATE_API_KEY}" "http://127.0.0.1:${PORT}/readiness"
printf '\n'
curl -fsS -H "Authorization: Bearer ${RMS_RELEASE_GATE_API_KEY}" "http://127.0.0.1:${PORT}/ops/warmup"
printf '\n'
