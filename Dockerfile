# ──────────────────────────────────────────────────────────────────────────
# Repo Management Suite — production Docker image
# Multi-stage: builder installs deps, runtime runs the API server.
# ──────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY repo_mgmt/ ./repo_mgmt/

# Install into a prefix we can copy wholesale
RUN pip install --no-cache-dir --prefix=/install .


# ── Runtime stage ──────────────────────────────────────────────────────────

FROM python:3.11-slim AS runtime

# Non-root user for security
RUN useradd --create-home --shell /bin/bash rms

WORKDIR /app

# System dependency: git (for GitPython)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY --from=builder /build/repo_mgmt ./repo_mgmt/

# Owned by non-root
RUN chown -R rms:rms /app
USER rms

# Environment defaults (override at runtime via --env-file or -e)
ENV RMS_HOST=0.0.0.0
ENV RMS_PORT=8000
ENV LOG_LEVEL=info
ENV RMS_DRY_RUN=true

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["rms-api"]
