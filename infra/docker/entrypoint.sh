#!/bin/sh
# Container entrypoint. "serve" applies database migrations (idempotent, optional) and starts the API + dashboard;
# any other arguments are passed to the `sentinel` CLI (e.g. `sources refresh --all`, `users create ...`).
set -eu
if [ "${1:-serve}" = "serve" ]; then
  # Single-instance deployments migrate on start; multi-replica deployments set SENTINEL_MIGRATE_ON_START=false
  # and run the migration Job (infra/k8s/jobs.yaml) before rollout to avoid concurrent upgrades.
  if [ "${SENTINEL_MIGRATE_ON_START:-true}" = "true" ]; then sentinel db upgrade; fi
  exec uvicorn sentinel.api.main:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}" \
       --workers "${SENTINEL_WEB_WORKERS:-1}" --proxy-headers --no-server-header
fi
exec sentinel "$@"
