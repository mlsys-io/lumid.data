FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md /app/
COPY src /app/src
COPY sdk /app/sdk

RUN pip install --upgrade pip && pip install "./sdk" && pip install "."

ENV LUMID_DATA_HTTP_PORT=9100
EXPOSE 9100

HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${LUMID_DATA_HTTP_PORT}/healthz" || exit 1

# Shell form so ${LUMID_DATA_HTTP_PORT} interpolates at start.
CMD uvicorn --factory lumid_data.server.main:create_app \
    --host 0.0.0.0 --port "${LUMID_DATA_HTTP_PORT}"
