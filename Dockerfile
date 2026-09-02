# ──────────────────────────────────────────────────────────────────────────
# Repository Automation Management Service — production Docker image
# Runtime includes Python, Git, Node.js 22.x, and npm for target validation.
# ──────────────────────────────────────────────────────────────────────────

FROM node:22.22.0-bookworm-slim AS node-runtime

FROM python:3.14-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.lock .
COPY repo_mgmt/ ./repo_mgmt/

RUN pip install --no-cache-dir --prefix=/install -r requirements.lock \
    && pip install --no-cache-dir --prefix=/install --no-deps .


# ── Runtime stage ──────────────────────────────────────────────────────────

FROM python:3.14-slim AS runtime

WORKDIR /app

# Git is required for GitPython/live branch operations. ca-certificates keeps
# HTTPS checks and package validation commands from tripping over missing roots.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# Bring in Node.js 22.x and npm without relying on distro packages that may lag
# below the required major version for the SEO/AEO/GEO validation command.
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

COPY --from=builder /install /usr/local
COPY --from=builder /build/repo_mgmt ./repo_mgmt/

# Non-root user for normal API operation.
RUN useradd --create-home --shell /bin/bash rms \
    && chown -R rms:rms /app \
    && python --version \
    && git --version \
    && node --version \
    && npm --version \
    && node -e "process.exit(Number(process.versions.node.split('.')[0]) === 22 ? 0 : 1)"

USER rms

# Production non-secret runtime configuration is version-controlled in this image.
# Koyeb should bind only the secrets/sensitive values documented in
# RAMS-KOYEB-PRODUCTION-ENV.txt; service-level non-secret env duplication is not required.
ENV APP_ENV=production \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=random \
    MALLOC_ARENA_MAX=2 \
    WEB_CONCURRENCY=1 \
    UVICORN_WORKERS=1 \
    UV_THREADPOOL_SIZE=2 \
    NODE_OPTIONS=--max-old-space-size=256 \
    LOG_LEVEL=info

ENV R2_ENDPOINT=https://3fb60a7136e950a7ec74959b45e4635e.r2.cloudflarestorage.com \
    R2_REGION=auto \
    R2_BUCKET_AUDITS=audits \
    R2_BUCKET_HIVE_SKILLS=hive-skills \
    R2_PUBLIC_BASE_URL_AUDITS=https://pub-f6b6cfd7d07e46f695d08e4a8dc3bd6b.r2.dev \
    R2_PUBLIC_BASE_URL_HIVE_SKILLS=https://pub-da50a6512f164566955a3076a1c795ef.r2.dev

ENV OPENROUTER_API_BASE=https://openrouter.ai/api/v1 \
    OPENROUTER_HTTP_REFERER=https://jonathan-harris.online \
    OPENROUTER_APP_NAME=RAMS \
    OPENROUTER_PRIMARY_MODEL=anthropic/claude-sonnet-4-6 \
    OPENROUTER_SECONDARY_MODEL=openai/gpt-4o-mini \
    OPENROUTER_TRIAGE_MODEL=google/gemini-2.5-flash-lite \
    RMS_OPENROUTER_CONNECT_TIMEOUT_SECONDS=5 \
    RMS_OPENROUTER_READ_TIMEOUT_SECONDS=90 \
    RMS_OPENROUTER_WRITE_TIMEOUT_SECONDS=30 \
    RMS_OPENROUTER_POOL_TIMEOUT_SECONDS=5 \
    RMS_OPENROUTER_MAX_CONNECTIONS=2 \
    RMS_OPENROUTER_MAX_KEEPALIVE_CONNECTIONS=1 \
    RMS_OPENROUTER_KEEPALIVE_EXPIRY_SECONDS=30 \
    RMS_OPENROUTER_MAX_RETRIES=0 \
    RMS_OPENROUTER_RETRY_BASE_SECONDS=1 \
    RMS_OPENROUTER_RETRY_MAX_SECONDS=8 \
    RMS_OPENROUTER_PROVIDER_SORT=price \
    RMS_OPENROUTER_ALLOW_FALLBACKS=true \
    RMS_OPENROUTER_DATA_COLLECTION=deny \
    RMS_PRIMARY_MAX_TOKENS=6144 \
    RMS_SECONDARY_MAX_TOKENS=3072 \
    RMS_TRIAGE_MAX_TOKENS=128 \
    RMS_PRIMARY_TEMPERATURE=0 \
    RMS_TRIAGE_TEMPERATURE=0 \
    RMS_TOP_P=0.9 \
    RMS_OPENROUTER_LOG_USAGE=true \
    RMS_OPENROUTER_LOG_COST=true \
    RMS_OPENROUTER_LOG_PROMPTS=false

ENV RMS_REPO_BOOTSTRAP_ENABLED=true \
    RMS_REPO_BASE_DIR=/tmp/rams-repos \
    RMS_WEBSITE_REPO_BRANCH=main \
    RMS_WEBSITE_REPO_PATH=/tmp/rams-repos/website \
    RMS_AIMS_REPO_BRANCH=main \
    RMS_AIMS_REPO_PATH=/tmp/rams-repos/aims \
    RMS_GITHUB_API_BASE=https://api.github.com \
    RMS_GITHUB_API_TIMEOUT_SECONDS=20 \
    RMS_GITHUB_API_MAX_RETRIES=2 \
    RMS_GIT_CLONE_DEPTH=1 \
    RMS_GIT_TIMEOUT_SECONDS=120 \
    RMS_GIT_OUTPUT_MAX_BYTES=65536

ENV RMS_WEBSITE_VALIDATION_COMMANDS="python3 scripts/inject_partials.py --validate && python3 scripts/sync_redirects.py --check && python3 scripts/check_crawlers.py"
ENV RMS_AIMS_VALIDATION_COMMANDS="npm test && npm run build"

ENV RMS_VALIDATION_TIMEOUT_SECONDS=240 \
    RMS_VALIDATION_OUTPUT_MAX_LINES=120 \
    RMS_VALIDATION_OUTPUT_MAX_BYTES=131072 \
    RMS_MAX_CONCURRENT_PIPELINES=1 \
    RMS_MAX_ISSUES_PER_RUN=1 \
    RMS_MAX_AUDIT_ARTEFACTS=8 \
    RMS_MAX_AUDIT_OBJECT_BYTES=1048576 \
    RMS_MAX_AUDIT_TOTAL_BYTES=4194304 \
    RMS_MAX_CONTEXT_FILES=8 \
    RMS_MAX_CONTEXT_FILE_BYTES=131072 \
    RMS_MAX_CONTEXT_TOTAL_BYTES=524288 \
    RMS_MAX_INDEXED_FILES=20000 \
    RMS_REPORT_MAX_BYTES=4194304 \
    RMS_MIN_FREE_DISK_MB=256 \
    RMS_TEMP_CLEANUP_ENABLED=true \
    RMS_TEMP_MAX_AGE_HOURS=24 \
    RMS_READINESS_CACHE_SECONDS=60 \
    RMS_IDEMPOTENCY_CACHE_SIZE=128 \
    RMS_BUSY_RETRY_AFTER_SECONDS=60 \
    RMS_SHUTDOWN_GRACE_SECONDS=25

ENV RMS_DRY_RUN=false \
    RMS_LIVE_WRITE_ENABLED=true \
    RMS_REPORT_PREFIX=qa-suite/reports \
    RMS_REPORT_DIR=/tmp/rams-reports \
    RMS_QA_BRANCH_PREFIX=rms-qa/ \
    RMS_PUSH_ENABLED=false \
    RMS_CREATE_PR=false \
    RMS_VALIDATE_AFTER_EACH_TASK=true \
    RMS_REVERT_ON_VALIDATION_FAILURE=true \
    RMS_SINGLE_WORKER_MODE=true \
    RMS_ALLOW_UNAUTHENTICATED_DEV=false \
    RMS_HOST=0.0.0.0 \
    RMS_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:' + __import__('os').getenv('RMS_PORT', __import__('os').getenv('PORT', '8000')) + '/health').raise_for_status()"

CMD ["rms-api"]
