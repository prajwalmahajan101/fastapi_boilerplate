# Testing

Operator-facing companion to [`tests/CLAUDE.md`](../tests/CLAUDE.md).
That file is the convention sheet. This one is the runbook.

## Three tiers

| Tier | What it proves | Services needed |
|---|---|---|
| `unit` | A helper or single class behaves in isolation. | none |
| `integration` | One layer round-trips against a real backing store. | Postgres **or** Redis |
| `e2e` | A real HTTP request flows through middleware → service → repository → audit. | Postgres + Redis |

Markers are auto-applied by directory (`tests/<tier>/...`). See
[`tests/CLAUDE.md`](../tests/CLAUDE.md) for the decision tree.

## Running locally

### Unit only (default fast loop)

```bash
pytest -m unit
```

No services. Sub-second. Run this on every change.

### Integration

```bash
docker compose up postgres redis db-init -d
pytest -m integration
```

`db-init` runs `alembic upgrade head` once Postgres is healthy, so
integration tests can assume the schema is current. Tests that need a
service that isn't up will `pytest.skip` cleanly — no failures, no
hangs.

Override the DSN / URL if you point at a non-default cluster:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:5432/db \
TEST_REDIS_URL=redis://host:6379/1 \
pytest -m integration
```

### E2E

```bash
docker compose up postgres redis db-init -d
pytest -m e2e
```

### Full suite

```bash
docker compose up postgres redis db-init -d
pytest
```

## Coverage

Wired but **non-blocking** — no `--cov-fail-under` yet.

```bash
pytest --cov=src --cov-report=term-missing --cov-report=html
open htmlcov/index.html
```

Coverage configuration lives in `pyproject.toml` under
`[tool.coverage.*]`: source = `src`, branch coverage on, omits
`__init__.py` and `src/management/init_db.py`.

### Future coverage gates

These thresholds are not enforced today — they're the target the
suite is being populated against. Add `--cov-fail-under` (and the
per-package floors below via `coverage-fail-under-package` or the
`Makefile`) once each target is reachable.

| Scope | Target |
|---|---|
| Overall | **85%** |
| `src/core/` | **90%** |
| `src/core/api_log/` | **95%** |
| `src/api/`, `src/service/`, `src/repository/` | **80%** |

The `api_log` floor is intentionally higher than the rest of `core`
because the audit pipeline is fire-and-forget — a regression that
silently swallows logs is invisible at runtime, so coverage is the
only safety net.

## Debugging a failing integration test

1. **Reproduce locally**: `pytest -m integration -x -k <name>`.
2. **Check the services**: `docker compose ps` (Postgres + Redis
   should be `healthy`).
3. **Inspect the test fixture output**: integration fixtures flush
   Redis and roll back Postgres per-test, so the state you see in
   the store is exactly what your test wrote — connect with
   `redis-cli` / `psql` to confirm.
4. **Did the test write?**: if the round-trip looks empty, check the
   key prefix or table name — fixtures use the same defaults the
   app would.

## Adding a test

1. Pick the tier using the decision tree in
   [`tests/CLAUDE.md`](../tests/CLAUDE.md).
2. Open the matching leaf `README.md` (e.g.
   `tests/integration/repository/README.md`).
3. Copy the exemplar named in the README.
4. Pin one property per test; name the test for the property.

## CI

There is no CI workflow shipped with this repo yet. The suite is
designed so a future workflow can simply run:

```bash
pytest -m unit --cov=src --cov-report=xml
docker compose up postgres redis db-init -d
pytest -m integration --cov=src --cov-append --cov-report=xml
pytest -m e2e --cov=src --cov-append --cov-report=xml
```

…and upload `coverage.xml` + `htmlcov/`. When you wire that up, also
introduce the future gates above.
