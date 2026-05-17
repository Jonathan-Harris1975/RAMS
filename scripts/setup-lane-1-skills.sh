#!/usr/bin/env bash
set -Eeuo pipefail

# Installs the full Lane 1 skills set for the Jonathan Harris AIMS/RAMS/website ecosystem.
# The repository-side governance files are committed separately; this script performs the external Skills.sh install.

run() {
  printf '
▶ %s
' "$*"
  DISABLE_TELEMETRY=1 "$@"
}

if ! command -v npx >/dev/null 2>&1; then
  echo "npx is required to install Skills.sh skills." >&2
  exit 1
fi

run npx --yes skills@latest add https://github.com/coreyhaines31/marketingskills --skill seo-audit ai-seo -y
run npx --yes skills@latest add https://github.com/vercel-labs/agent-browser -y
run npx --yes skills@latest add https://github.com/currents-dev/playwright-best-practices-skill -y
run npx --yes skills@latest add https://github.com/anthropics/skills --skill webapp-testing xlsx pdf -y
run npx --yes skills@latest add https://github.com/firecrawl/cli --skill firecrawl-crawl firecrawl-scrape firecrawl-search -y
run npx --yes skills@latest add https://github.com/obra/superpowers --skill verification-before-completion -y
run npx --yes skills@latest add https://github.com/cloudflare/skills --skill web-perf -y
run npx --yes skills@latest add https://github.com/sentry/dev --skill sentry-cli -y
run npx --yes skills@latest add https://github.com/browser-use/browser-use -y

printf '
✅ Lane 1 Skills.sh install commands completed. Review .agents/lane-1-skills.json before enabling scheduled automation.
'
