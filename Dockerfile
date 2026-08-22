# ──────────────────────────────────────────────────────────────────────────
# Repository Automation Management Service — production Docker image
# Runtime includes Python, Git, Node.js >=20, and npm for target validation.
# ──────────────────────────────────────────────────────────────────────────

FROM node:26-bookworm-slim AS node-runtime

FROM python:3.14-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY repo_mgmt/ ./repo_mgmt/

RUN pip install --no-cache-dir --prefix=/install .


# ── Runtime stage ──────────────────────────────────────────────────────────

FROM python:3.14-slim AS runtime

WORKDIR /app

# Git is required for GitPython/live branch operations. ca-certificates keeps
# HTTPS checks and package validation commands from tripping over missing roots.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

# Bring in Node.js 20.x and npm without relying on distro packages that may lag
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
    && node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 20 ? 0 : 1)"

USER rms

# Conservative defaults for Koyeb eco-micro (0.25 vCPU / 512MB RAM).
# Koyeb environment variables may override these without rebuilding the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=random \
    MALLOC_ARENA_MAX=2 \
    WEB_CONCURRENCY=1 \
    UVICORN_WORKERS=1 \
    UV_THREADPOOL_SIZE=2 \
    NODE_OPTIONS=--max-old-space-size=256 \
    RMS_HOST=0.0.0.0 \
    RMS_PORT=8000 \
    LOG_LEVEL=info \
    RMS_ENVIRONMENT=production \
    RMS_DRY_RUN=true \
    RMS_LIVE_WRITE_ENABLED=false \
    RMS_MAX_CONCURRENT_PIPELINES=1 \
    RMS_MAX_ISSUES_PER_RUN=1 \
    RMS_SINGLE_WORKER_MODE=true

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:' + __import__('os').getenv('RMS_PORT', __import__('os').getenv('PORT', '8000')) + '/health').raise_for_status()"

CMD ["rms-api"]
