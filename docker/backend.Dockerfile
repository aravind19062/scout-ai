FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app

# Optional dependency groups. "rerank" adds the local ONNX cross-encoder (fastembed); build with
# --build-arg EXTRAS= for a slimmer image without it.
ARG EXTRAS="rerank"

# Dependencies first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project $(for e in $EXTRAS; do printf -- '--extra %s ' "$e"; done)

# Bake the reranker model into the image so containers never download it at runtime.
ENV FASTEMBED_CACHE_PATH=/app/.cache/fastembed
ARG PRELOAD_RERANKER_MODEL="Xenova/ms-marco-MiniLM-L-6-v2"
RUN if [ -n "$PRELOAD_RERANKER_MODEL" ] && echo "$EXTRAS" | grep -q rerank; then \
      .venv/bin/python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder as T; T('$PRELOAD_RERANKER_MODEL')"; \
    fi

COPY . .
RUN useradd --create-home --uid 10001 scout && chown -R scout /app
USER scout
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health')"
# Apply migrations, then serve. With JOB_EXECUTION=inline (default) research jobs run in this process;
# with JOB_EXECUTION=worker they run in separate `python -m app.worker` containers (see docker-compose.prod.yml).
# Client IPs: uvicorn trusts X-Forwarded-For only from FORWARDED_ALLOW_IPS (default 127.0.0.1).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
