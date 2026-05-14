#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"

printf 'GET /health\n'
curl -sS "$BASE_URL/health"
printf '\n\nGET /readiness\n'
curl -sS "$BASE_URL/readiness"

for pipeline in seo-aeo-geo mobile-ux on-brand; do
  printf '\n\nPOST /rebuild/%s/run\n' "$pipeline"
  curl -sS -X POST "$BASE_URL/rebuild/$pipeline/run" \
    -H 'Content-Type: application/json' \
    -d '{"dry_run":true}'
done
printf '\n'
