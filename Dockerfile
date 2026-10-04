# syntax=docker/dockerfile:1.7
# ---------- dashboard build ----------
FROM node:22-alpine AS dashboard
WORKDIR /ui
COPY dashboard/package.json dashboard/package-lock.json ./
# Optional build secret "build_ca": extra CA bundle for builds behind a TLS-inspecting corporate proxy.
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -f /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca; fi; \
    npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

# ---------- python wheels ----------
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip wheel --wheel-dir /wheels ".[postgres]"

# ---------- runtime ----------
FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.title="sentinel-dd" \
      org.opencontainers.image.description="Sentinel due-diligence & third-party risk intelligence (API + analyst dashboard)" \
      org.opencontainers.image.licenses="MIT"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SENTINEL_ENV=prod \
    SENTINEL_DATA_DIR=/data \
    SENTINEL_DATABASE_URL=sqlite:////data/sentinel.db \
    SENTINEL_CHECKPOINT_URL=/data/checkpoints.sqlite \
    SENTINEL_DASHBOARD_DIR=/app/dashboard
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home /app app \
    && mkdir -p /data /app && chown app:app /data
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
COPY --from=dashboard /ui/dist /app/dashboard
COPY infra/docker/entrypoint.sh /usr/local/bin/sentinel-entrypoint
RUN chmod 0755 /usr/local/bin/sentinel-entrypoint
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
ENTRYPOINT ["sentinel-entrypoint"]
CMD ["serve"]
