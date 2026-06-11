# tests/ — three-tier test suite

> Per-tier purpose, the decision tree for picking a tier, the fixture
> inventory, and the marker convention. New tests follow this. Old
> flat tests have already been moved into the tiers.

## Tiers

| Tier | Scope | Touches | Speed | Marker |
|---|---|---|---|---|
| **unit** | Pure helper / single class / no I/O. | nothing external | <1s total | `unit` (auto) |
| **integration** | One layer against one real backing store. | Postgres **or** Redis | seconds | `integration` (auto) |
| **e2e** | Full HTTP request through middleware + service + repo + audit. | Postgres + Redis | seconds-tens | `e2e` (auto) |

Markers are applied automatically by directory — `pytest_collection_modifyitems`
in `tests/conftest.py` tags every test under `tests/<tier>/...` with
`@pytest.mark.<tier>`. Don't apply the marker by hand.

## Picking a tier

```
Does the code under test do any I/O of its own?
├── No  → unit
└── Yes → does it touch more than one layer
          (middleware + route + service + repo)?
          ├── No  → integration  (one layer + one store)
          └── Yes → e2e          (full HTTP request)
```

If you're unsure between integration and e2e: prefer integration —
it's faster, easier to debug, and most "does this layer work"
questions are answerable without driving the whole HTTP path.

## Subsetting

```bash
pytest -m unit            # fast feedback, no services needed
pytest -m integration     # needs `docker compose up postgres redis -d`
pytest -m e2e             # full stack, plus `alembic upgrade head`
pytest                    # all three
pytest --cov=src --cov-report=term-missing   # with coverage
```

## Fixture inventory

**Root `tests/conftest.py`** — applies to every tier:

- `_bind_settings` (session, autouse) — calls `core.runtime.configure(settings)`.
- `_reset_singletons` (per-test, autouse) — drops every cached process
  singleton via `reset_all_singletons`.
- `client` — `TestClient(app)` **without** lifespan. Resilience falls
  back to in-memory and the audit backend to no-op. Use for fast
  middleware/envelope smoke tests.

**`tests/integration/conftest.py`** — opt-in fixtures:

- `pg_dsn` (session) — Postgres DSN, auto-skips when unreachable.
- `redis_url` (session) — Redis URL, auto-skips when unreachable.
- `redis_client` (per-test) — flushed `redis.asyncio` client.
- `pg_engine` (per-test) — async SQLAlchemy engine, disposed on teardown.

**`tests/e2e/conftest.py`** — opt-in fixtures:

- `live_client` — `TestClient(app)` **with** lifespan engaged. Use
  when you need the real startup wiring.

## Exemplars to copy from

- Unit: [`unit/utils/test_pagination.py`](unit/utils/test_pagination.py)
- Integration: [`integration/resilience/test_throttle_redis_exemplar.py`](integration/resilience/test_throttle_redis_exemplar.py)
- E2E: [`e2e/test_hello_smoke.py`](e2e/test_hello_smoke.py)

Each leaf directory under the three tiers also has a `README.md`
naming its scope, the matching production-code path, and a recipe.

## Coverage

Enforced — `pytest --cov` fails the run below the floor
(`pytest.ini` `addopts = --cov-fail-under=85` +
`pyproject.toml` `[tool.coverage.report] fail_under = 85`). Per-package
floors are enforced in CI as follow-up
`coverage report --include=<glob> --fail-under=N` steps. The unit-only
fast loop (`pytest -m unit` without `--cov`) is unaffected. Generate
locally:

```bash
pytest --cov=src --cov-report=html --cov-report=term-missing
open htmlcov/index.html
```

The enforced coverage floors — overall via pytest/coverage config,
per-package via CI follow-up steps — are recorded in
[`docs/testing.md`](../docs/testing.md):

| Scope | Target |
|---|---|
| Overall | 85% |
| `src/core/` | 90% |
| `src/core/api_log/` | 95% |
| `src/api/`, `src/service/`, `src/repository/` | 80% |

## Don'ts

- Don't apply tier markers by hand — directory placement is the
  source of truth.
- Don't mock Postgres or Redis in integration tests. The point of
  the tier is the round-trip.
- Don't write a new test directly under `tests/` — pick the right
  tier directory.
- Don't `import` from another tier's conftest — fixtures cascade
  down from the root, not sideways.
