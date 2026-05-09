FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock README.md /app/
COPY sdk/pyproject.toml sdk/README.md /app/sdk/

RUN uv sync --locked --no-dev --no-install-project --no-install-package lumid-data-sdk

COPY src /app/src
COPY sdk/src /app/sdk/src

RUN uv sync --locked --no-dev --no-editable

ENV LUMID_DATA_HTTP_PORT=9100
EXPOSE 9100

HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${LUMID_DATA_HTTP_PORT}/healthz" || exit 1

CMD ["sh", "-c", "exec uvicorn --factory lumid_data.server.main:create_app --host 0.0.0.0 --port \"$LUMID_DATA_HTTP_PORT\""]
