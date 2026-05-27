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
