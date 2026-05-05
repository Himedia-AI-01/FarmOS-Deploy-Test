# Design Ref: §16.1 — farmos-ec2-deploy Module 1
# Multi-stage build: slim base + uid 1000 + tini, with HF model preloaded for cold-start.
# Plan SC: backend image <= 3GB, non-root execution, /health 200 within 60s.

# ============================================================
# Stage 1: builder
#   uv install + dependency compile + sentence-transformers preload
# ============================================================
FROM python:3.12-slim AS builder

ARG UV_VERSION=0.5.11
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libffi-dev \
        libjpeg-dev \
        zlib1g-dev \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/${UV_VERSION}/install.sh | sh \
 && ln -s /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /app

# Layer cache: deps before source
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project

COPY backend/ ./

# Install project (registers subsidy-ingest CLI)
RUN uv sync --frozen

# Preload sentence-transformers model — kills the 5-min cold start (R5)
ENV HF_HOME=/opt/hf-cache
RUN /opt/venv/bin/python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

# ============================================================
# Stage 2: runtime
#   slim + non-root uid 1000 + venv + HF cache only
# ============================================================
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/opt/hf-cache \
    CHROMA_PERSIST_DIR=/app/chroma_data \
    APP_PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1000 appgroup \
 && useradd  --system --uid 1000 --gid appgroup --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/hf-cache /opt/hf-cache
COPY --from=builder --chown=appuser:appgroup /app /app

# ChromaDB persist dir — mounted as host volume in compose
RUN mkdir -p /app/chroma_data && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

# Design Ref: §16.1 HEALTHCHECK — /health includes DB ping (existing behavior accepted)
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# tini = PID 1 (zombie reaper)
ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
