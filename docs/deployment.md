# Deployment

Sentinel ships as one container image (`Dockerfile`) that contains the API, the background case runner, the CLI and
the built dashboard. It needs PostgreSQL and a persistent volume for the raw-source store, model registry and (if
not using PostgreSQL for them) LangGraph checkpoints.

## Image

```bash
docker build -t sentinel-dd:2.0.0 .
# behind a TLS-inspecting proxy, pass its CA as a build secret (not baked into the image):
docker build --secret id=build_ca,src=/path/to/corporate-ca.pem -t sentinel-dd:2.0.0 .
```

Stages: Node 22 builds the dashboard → Python 3.12 builds wheels (`.[postgres]`) → slim runtime running as UID
10001, `HEALTHCHECK` on `/health`, `SENTINEL_ENV=prod` by default (so it refuses insecure configuration).

Entrypoint (`infra/docker/entrypoint.sh`):

* `serve` (default): `sentinel db upgrade` (unless `SENTINEL_MIGRATE_ON_START=false`), then uvicorn on `$PORT`
  (8000) with `$SENTINEL_WEB_WORKERS` processes (default 1).
* anything else is passed to the `sentinel` CLI, e.g. `docker run … sentinel-dd:2.0.0 sources refresh --all`.

## Docker Compose (single host, PostgreSQL)

```bash
cp .env.example .env
# set: SENTINEL_JWT_SECRET, SENTINEL_SEC_USER_AGENT, POSTGRES_PASSWORD
docker compose up -d --build
docker compose run --rm api sources refresh --all
docker compose run --rm api ml train --year 2024
docker compose run --rm -e SENTINEL_NEW_PASSWORD='…' api users create --username alice --roles analyst
docker compose run --rm -e SENTINEL_NEW_PASSWORD='…' api users create --username bob --roles reviewer
open http://localhost:8000
docker compose --profile observability up -d prometheus   # optional
```

The API container runs read-only with `no-new-privileges`, all capabilities dropped and a tmpfs `/tmp`.

This compose stack was exercised for this release: image build, migrations on PostgreSQL 16, live download of the
five watch lists, peer-model training on live SEC frames, five live investigations through the API, review
decisions and audit verification — see [evaluation.md](evaluation.md#5-live-end-to-end-verification).

## Kubernetes

`infra/k8s/` contains reference manifests (not a Helm chart):

| File | Contents |
|---|---|
| `configmap.yaml` | non-secret settings (`SENTINEL_MIGRATE_ON_START=false`) and a **template** Secret — populate from a secret manager (e.g. External Secrets Operator); never commit values |
| `jobs.yaml` | `sentinel-migrate` Job (run before each rollout), `sentinel-watchlist-refresh` CronJob (every 6 h), `sentinel-data` PVC (ReadWriteMany) |
| `deployment.yaml` | Deployment (non-root, read-only root FS, dropped capabilities, seccomp, probes on `/ready` and `/health`), Service, HPA, PodDisruptionBudget, NetworkPolicy (ingress from the ingress namespace; egress to PostgreSQL and HTTPS/DNS) |

Order: create Secret + ConfigMap → PVC → migration Job → Deployment. Replace `ghcr.io/your-org/sentinel-dd`
with your registry. The manifests are parsed and structurally checked by `tests/unit/test_infra.py` (kinds, image
tags, security contexts, probes, entrypoint arguments) but have not been applied to a live cluster for this release.

### Multi-replica considerations

* Use PostgreSQL for both `SENTINEL_DATABASE_URL` and `SENTINEL_CHECKPOINT_URL` so any replica can resume a case
  after a review decision.
* The volume must be shared (ReadWriteMany) because evidence lineage points into the raw store.
* Rate limiting is per process; put a gateway/WAF rate limit in front for a global limit.
* Each replica runs the cases it accepted; on restart, unfinished cases are marked failed and can be retried
  (`POST /api/v1/cases/{id}/retry`). A queue-based runner is listed in [limitations.md](limitations.md).

## Production checklist

* [ ] `SENTINEL_ENV=prod`, strong `SENTINEL_JWT_SECRET` from a secret manager, rotated on a schedule
* [ ] PostgreSQL with TLS, backups and PITR ([disaster_recovery.md](disaster_recovery.md))
* [ ] `SENTINEL_SEC_USER_AGENT` with a monitored contact address
* [ ] TLS termination at the ingress; HSTS is already sent by the app
* [ ] watch-list refresh scheduled (CronJob) and `/ready` + freshness alerts wired to monitoring
* [ ] jurisdiction policy reviewed by Compliance (`SENTINEL_JURISDICTION_POLICY_PATH`)
* [ ] user accounts created per person (no shared accounts); auditor role for 2nd/3rd line
* [ ] if an LLM provider is enabled: data-processing agreement, key in the secret store, cost alerts on `llm_cost_usd`
* [ ] OpenSanctions PEP data only with a commercial licence
