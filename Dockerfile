# syntax=docker/dockerfile:1.7
# ---------- builder ----------
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --wheel-dir /wheels ".[openai,mlops]"

# ---------- runtime ----------
FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.title="sentinel-dd" \
      org.opencontainers.image.description="Governed agentic due-diligence platform"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SENTINEL_ENV=prod SENTINEL_DATA_DIR=/app/data SENTINEL_MODEL_DIR=/app/var/models SENTINEL_AUDIT_LOG_PATH=/app/var/audit.log
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
COPY data ./data
# train + register the champion model at build time so the image is self-contained & reproducible
RUN python -m sentinel.ml.train --model-dir /app/var/models && chown -R app:app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
CMD ["uvicorn", "sentinel.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
