# Known issues — pre-existing test failures

Tracking the small set of test failures that are **not** introduced by an
in-flight PR. New PRs may keep these red as long as the relevant section
below has not changed. Remove an entry the same commit that fixes it.

## Pre-existing as of `feat/depend-on-resilience-kit` (PR #6) — M8b kit upgrade

Both failures reproduce when the affected test file is reset to `main`,
so they predate the kit upgrade. They only surface against live
Postgres + Redis (the unit tier stays green), which is why nobody had
seen them yet — the integration tier had never been run against the
docker stack on this branch.

### 1. `test_first_signin_attaches_default_role` — `MissingGreenlet`

- **File:** `tests/integration/auth/test_oauth_default_role.py`
- **Failure:** `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been
  called; can't call await_only() here. Was IO attempted in an unexpected
  place?`
- **Cause (suspected):** the test fixture mixes sync SQLAlchemy with the
  async OAuth signin path. Either the fixture's session is sync and the
  code-under-test wants async, or a `selectinload` triggers a lazy fetch
  outside the async context manager.
- **Scope:** affects `test_first_signin_attaches_default_role` directly;
  the other two cases in the same file (`test_returning_user_keeps_existing_roles`,
  `test_missing_default_role_logs_warning`) error from the same root in
  setup.
- **Investigation start:** read the test's session fixture vs.
  `src/auth/oauth_google.py::authenticate` — check that both use the
  same async session shape.

### 2. `test_concurrent_revoke_produces_one_winner` — assertion order

- **File:** `tests/integration/service/test_apikey_revoke_concurrency.py`
- **Failure:** `AssertionError: assert [(True, False), (True, False)] ==
  [(False, True), (True, False)]`
- **Cause (suspected):** the test fires two concurrent `revoke()` calls
  against the same API key and asserts exactly one wins. The observed
  output shows **both** sessions reporting success, which means either:
  - **(a)** the row-lock in
    `src/service/auth.py::APIKeyService.revoke` (commit `67bd56d`) has a
    race the test catches, OR
  - **(b)** the assertion is too strict about *which* session wins (the
    test compares against a fixed `[(False, True), (True, False)]`
    rather than allowing either order).
- **Triage:** investigate (a) first. If the row-lock genuinely allows
  both to succeed, it's a real defect — fix the service, keep the
  assertion. If it's (b), relax to `sorted(results) == [(False, True),
  (True, False)]` so either scheduling order passes but a double-winner
  still fails the test.

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
