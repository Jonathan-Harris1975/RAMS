#!/usr/bin/env bash
set -euo pipefail

export DISABLE_TELEMETRY="${DISABLE_TELEMETRY:-1}"

printf 'Installing Batch 1 Skills.sh search visibility skills...\n'
printf 'Repository: %s\n' "$(pwd)"

npx --yes skills@latest add coreyhaines31/marketingskills --skill seo-audit ai-seo -y

printf '\nBatch 1 skills installed. Local governance skill remains at .agents/skills/batch-1-search-visibility/SKILL.md\n'
