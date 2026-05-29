# Development

The day-to-day local workflow.

## Boot

```bash
# Bring up Postgres + Redis
docker compose up -d postgres redis

# Install runtime + dev deps
pip install -r requirements/dev.txt

# Apply migrations
alembic upgrade head

# Run the app
python main.py             # uvicorn on :8000
```

Browse `http://localhost:8000/docs` for the Swagger UI (enabled by
`LocalSettings`).

## Pre-commit

Install the hooks once:

```bash
pre-commit install
```

Hooks run on every staged change:

| Hook | Purpose |
|---|---|
| `ruff` | Lint + format. |
| `pydocstyle` + `darglint` | Docstring style. |
| `check_layering.py` | `src.core` must not import `src.common` / domain. |
| `check_dead_utils.py` | No public symbol in `src/core` without a caller. |
| `check_openapi_metadata.py` | Routes declare the required `responses=` set. |
| `check_stale_refs.py` | Doc references match real symbols. |
| `dump_settings_schema.py --check` | `docs/environment.md` matches `CoreSettings`. |
| `pip-compile --dry-run` | `requirements/*.in` and `*.txt` agree. |

To re-run all hooks against the whole tree:

```bash
pre-commit run --all-files
```

## Running tests

Three tiers, run from the project root:

```bash
pytest -m unit          # fast loop; no Postgres / Redis required
pytest -m integration   # one layer against real services (auto-skips if down)
pytest -m e2e           # full HTTP path through the stack
pytest                  # everything
```

See [`testing.md`](testing.md) and `tests/CLAUDE.md` for the
conventions and where to put a new test.

## Make targets

```bash
make dev      # install dev deps + pre-commit hooks
make audit    # pip-audit + check_dead_utils + check_layering + …
make test     # pytest -m "unit or integration"
```

## Common recipes

| Task | Command |
|---|---|
| New migration | `alembic revision --autogenerate -m "<msg>"` then review. |
| New route | Add module under `src/api/v1/`, wire in `v1/__init__.py`, update `openapi_metadata.py`. |
| New env var | Add field to `CoreSettings`, `python scripts/dump_settings_schema.py --write`. |
| New exception family | See [`exceptions.md`](exceptions.md). |
| Regenerate lockfiles | See [`dependency-management.md`](dependency-management.md). |
| Add an auth provider | See [`authentication.md`](authentication.md). |

## Hot-reload

`python main.py` runs uvicorn without reload by default. For a
hot-reload dev loop:

```bash
uvicorn src.app:app --reload
```

Lifespan startup runs on every reload — keep startup cheap (lazy
imports inside lifespan handlers, not at module top-level).
