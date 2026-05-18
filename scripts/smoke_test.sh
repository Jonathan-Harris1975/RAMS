#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
RMS_API_KEY="${RMS_API_KEY:-}"

if [[ -n "$RMS_API_KEY" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${RMS_API_KEY}")
else
  AUTH_ARGS=()
fi

printf 'GET /health\n'
curl -sS "$BASE_URL/health"
printf '\n\nGET /readiness\n'
curl -sS "${AUTH_ARGS[@]}" "$BASE_URL/readiness"

for pipeline in seo-aeo-geo mobile-ux on-brand; do
  printf '\n\nPOST /rebuild/%s/run\n' "$pipeline"
  curl -sS -X POST "$BASE_URL/rebuild/$pipeline/run" \
    "${AUTH_ARGS[@]}" \
    -H 'Content-Type: application/json' \
    -d '{"dry_run":true}'
done
printf '\n'
