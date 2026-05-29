# Deployment

Container-first. The `Dockerfile` builds a slim image; `docker-compose.yml`
brings up Postgres + Redis + app for local + smoke testing.

## Process model

Production: `uvicorn` workers behind a Gunicorn / Kubernetes service.

```bash
uvicorn src.app:app \
    --workers $(nproc) \
    --host 0.0.0.0 --port 8000 \
    --proxy-headers --forwarded-allow-ips='*'
```

Resilience providers cache one backend instance per worker — pick
Redis-backed tiers (cache, throttle, breaker) when you need shared
state. The pybreaker tier is per-worker by design.

## Required secrets (prod)

`ProdSettings` fails to boot without these:

| Env var | Purpose |
|---|---|
| `FIELD_ENCRYPTION_KEY` | Fernet key for `EncryptedString` columns. |
| `SECRET_KEY` | App-level signing secret. |
| `DB_HOST` (non-localhost) | Database host. |

Add when enabling JWT / OAuth:

| Env var | Required when |
|---|---|
| `JWT_SIGNING_KEY` | `"jwt"` in `AUTH_ENABLED_PROVIDERS`. |
| `GOOGLE_OAUTH_CLIENT_ID/SECRET/REDIRECT_URI` | `"oauth_google"` in `AUTH_ENABLED_PROVIDERS`. |

Recommended source: AWS Secrets Manager. Set `AWS_SECRET_NAME` and
the boilerplate's settings source overrides env automatically.

## Health probes

- Liveness: `GET /healthz` → 200 while the process is up.
- Readiness: `GET /readyz` → 200 only when every resilience
  provider's `is_healthy()` passes. Drain the pod on 503; the app
  keeps serving but degraded backends are surfaced.

Kubernetes liveness should *not* hit `/readyz` — a transient Redis
outage will then restart the pod and lose the in-memory fallback.

## Migrations

```bash
alembic upgrade head
```

Run as an init container or a pre-deploy hook. Migrations live
under `alembic/`; autogenerate via `alembic revision --autogenerate -m "<msg>"`,
review the diff, then commit.

## Audit-log backend

`api_log_backend=postgres` (default) reuses the same engine as the
app sessions — one connection pool. Set `api_log_backend=noop` for
read-only / smoke deployments where audit isn't required.

`api_log_drain_timeout_seconds` caps shutdown drain — keep it short
enough to satisfy Kubernetes `terminationGracePeriodSeconds`.

## Workers (Celery)

```bash
celery -A src.core.tasks:celery_app worker -Q default
```

Topology is documented in [`celery-topology.md`](celery-topology.md).
The broker is Redis (`task_redis_alias`); result backend defaults to
the same URL — override `CELERY_RESULT_BACKEND` to send results
elsewhere or disable.

## Profile selection

`APP_ENV=prod` selects `ProdSettings`. The smoke check on every
deploy:

```bash
APP_ENV=prod python -c "from src.common.settings import settings; \
    print(type(settings).__name__)"
```

A clean print is required before traffic is admitted.
