# src/db — engine lifecycle

> Thin starter notes.

- `lifecycle.py` — `init_db_engine` / `close_db_engine`, the lifespan
  hooks. They wrap `core.utils.db`'s DSN-keyed engine cache so the app, the
  `api_log` backend, and every request session share one pool.
- `tables.py` — imports every ORM model from `src.model` so
  `BaseModel.metadata` is fully populated for Alembic autogenerate and the
  `init_db` DDL bootstrap. **Add new models to `src.model`'s `__init__`**;
  they flow through here automatically.

No ORM models live in this package — models belong in `src/model`.

## Common pitfalls

- **Opening a second engine in a CLI / management script** — call
  `core.utils.db.get_app_engine()` instead; the DSN-keyed cache hands
  back the same pool the request path uses.
- **Forgetting to re-export a new model from `src/model/__init__.py`**
  — `src/db/tables.py` only sees what `src.model` exports, so a model
  added but not exported is invisible to Alembic autogenerate.
- **Closing the engine inside a request handler** — that would tear
  the pool out from under every other in-flight request. Engine
  lifecycle belongs to `src/app.py`'s lifespan, never to a handler.

## Reference example

`src/db/lifecycle.py` is the canonical "open / close one engine across
the whole app" wiring. Mirror it for any future per-DSN engine.

## Tests

Reference test directory: [`tests/integration/repository/`](../../tests/integration/repository/)
— integration tests against the real Postgres engine. See
[`tests/CLAUDE.md`](../../tests/CLAUDE.md) for the tier conventions.
