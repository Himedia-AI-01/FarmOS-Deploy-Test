# Multi-stage build for shopping_mall backend.
# Mirrors farmos backend/Dockerfile pattern: slim base + uid 1000 + tini.
# Differences from farmos:
#   - Port 4000 (farmos uses 8000)
#   - psycopg2 (sync) instead of asyncpg
#   - bge-m3 embedding model preload (~2.2GB) instead of MiniLM-L12-v2
#   - LangChain/LangGraph stack

# ============================================================
# Stage 1: builder
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

# Layer cache: deps before source.
# Build context = shopping_mall/backend/ (set via deploy.yml docker buildx -f)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . ./

RUN uv sync --frozen

# bge-m3 모델은 빌드 타임 preload 하지 않음 (~2.2GB, GH Actions runner 메모리/시간 한계).
# 컨테이너 첫 사용 시 /opt/hf-cache 에 자동 다운로드 (~1~2분).
# EC2 t3.large 의 AWS 내부망에서 HF 다운로드 빠름.
ENV HF_HOME=/opt/hf-cache
RUN mkdir -p /opt/hf-cache

# ============================================================
# Stage 2: runtime
# ============================================================
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/opt/hf-cache \
    APP_PORT=4000

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

# ChromaDB persist dir — shared volume mount in compose (RW with farmos-api)
RUN mkdir -p /app/chroma_data && chown -R appuser:appgroup /app

USER appuser

EXPOSE 4000

# /health endpoint defined in shopping_mall/backend/app/routers (per CLAUDE.md)
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:4000/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "4000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
