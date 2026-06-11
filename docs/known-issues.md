# Known issues — pre-existing test failures

Tracking the small set of test failures that are **not** introduced by an
in-flight PR. New PRs may keep these red as long as the relevant section
below has not changed. Remove an entry the same commit that fixes it.

## Pre-existing as of `feat/depend-on-resilience-kit` (PR #6) — M8b kit upgrade

_No tracked pre-existing failures at this time._

## Reproducing locally

Spin up isolated services (so the host's existing `postgres:5432` is
left alone):

```bash
docker run -d --rm --name rk-pg-test \
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=postgres \
  -p 5433:5432 postgres:16-alpine
docker run -d --rm --name rk-redis-test -p 6380:6379 redis:7-alpine

DB_HOST=localhost DB_PORT=5433 DB_USER=postgres DB_PASSWORD=postgres DB_NAME=postgres \
  .venv/bin/alembic upgrade head

FERNET_KEY=$(.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5433/postgres" \
TEST_REDIS_URL="redis://localhost:6380/0" \
RESILIENCE_CRYPTO__FIELD_ENCRYPTION_KEY="$FERNET_KEY" \
RESILIENCE_CRYPTO__ENVIRONMENT="dev" \
  .venv/bin/python -m pytest -m integration -v

# Teardown
docker stop rk-pg-test rk-redis-test
```
