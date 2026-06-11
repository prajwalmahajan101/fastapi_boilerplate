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

```bash
pytest --cov=src --cov-report=term-missing --cov-report=html
open htmlcov/index.html
```

Coverage configuration lives in `pyproject.toml` under
`[tool.coverage.*]`: source = `src`, branch coverage on, omits
`__init__.py` and `src/management/init_db.py`. The overall
`fail_under = 85` floor lives there too.

### Coverage gates (enforced)

`pytest --cov` fails the run when the overall floor is missed —
the gate is wired through `pytest.ini` (`addopts = --cov-fail-under=85`)
and `pyproject.toml` (`[tool.coverage.report] fail_under = 85`). CI
enforces the per-package floors as separate
`coverage report --include=<glob> --fail-under=N` steps after the
combined run (see `.github/workflows/test.yml`).

| Scope | Floor | Notes |
|---|---|---|
| Overall | **85%** | `pytest --cov` (uses `addopts`) |
| `src/core/` | **90%** | CI step: `coverage report --include="src/core/*" --fail-under=90` |
| `src/core/api_log/` | **95%** | CI step: `coverage report --include="src/core/api_log/*" --fail-under=95` |
| `src/api/`, `src/service/`, `src/repository/` | **80%** | CI step: `coverage report --include="src/api/*,src/service/*,src/repository/*" --fail-under=80` |

The `api_log` floor is intentionally higher than the rest of `core`
because the audit pipeline is fire-and-forget — a regression that
silently swallows logs is invisible at runtime, so coverage is the
only safety net.

The unit-only fast loop (`pytest -m unit` without `--cov`) is
unaffected: `--cov-fail-under` only triggers when `--cov` is on the
command line.

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

`.github/workflows/test.yml` runs on every push to `main` and every
pull request:

1. **lint job** — `ruff check .` + `ruff format --check .`.
2. **test job** — spins `postgres:16-alpine` and `redis:7-alpine` as
   GitHub Actions service containers, exports the env-var shape from
   [`known-issues.md`](known-issues.md) (`DB_*`, `TEST_DATABASE_URL`,
   `TEST_REDIS_URL`, `RESILIENCE_CRYPTO__*`), mints a fresh Fernet
   key, runs `alembic upgrade head`, then
   `pytest --cov --cov-report=term-missing --cov-report=xml`
   (exercises all three tiers because the service containers satisfy
   the integration / e2e auto-skip gate).
3. **per-package floors** — three follow-up
   `coverage report --include=<glob> --fail-under=N` steps enforce
   the table above.
4. **artifact** — `coverage.xml` is uploaded on every run.
